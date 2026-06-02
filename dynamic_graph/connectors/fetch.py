from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import socket
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from ._dates import parse_date
from ._http import _raise_for_status, async_retrying
from .contracts import FetchClient, FetchedPage

_USER_AGENT = "dynamic-graph/0.1"
_SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}
_BLOCKED_HOSTS = {"localhost"}
_METADATA_IP = ipaddress.ip_address("169.254.169.254")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_MAX_REDIRECTS = 5
_DATE_META = {"article:published_time", "og:article:published_time"}
_WS = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    """Extracts title, readable text and a handful of head metadata fields."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.published_raw: str | None = None
        self.canonical: str | None = None
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            self._handle_meta(dict(attrs))
        elif tag == "link":
            self._handle_link(dict(attrs))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Self-closing <meta .../> and <link .../> do not emit an end tag.
        if tag == "meta":
            self._handle_meta(dict(attrs))
        elif tag == "link":
            self._handle_link(dict(attrs))

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and self.title is None:
            text = data.strip()
            if text:
                self.title = text
        if self._skip_depth == 0:
            self._chunks.append(data)

    def _handle_meta(self, attrs: dict[str, str | None]) -> None:
        if self.published_raw is not None:
            return
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        content = attrs.get("content")
        if not content:
            return
        if key in _DATE_META or key == "date":
            self.published_raw = content

    def _handle_link(self, attrs: dict[str, str | None]) -> None:
        if self.canonical is None and (attrs.get("rel") or "").lower() == "canonical":
            href = attrs.get("href")
            if href:
                self.canonical = href

    @property
    def text(self) -> str:
        return _WS.sub(" ", " ".join(self._chunks)).strip()


class HttpFetchClient(FetchClient):
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 20.0,
        max_bytes: int = 2_000_000,
        max_chars: int = 40_000,
    ) -> None:
        self._owns_client = client is None
        # Redirects are followed manually so each hop can be re-validated.
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
        )
        self._max_bytes = max_bytes
        self._max_chars = max_chars

    async def fetch(self, url: str) -> FetchedPage:
        response = await self._get_following_redirects(url)

        raw_bytes = response.content[: self._max_bytes]
        content_type = response.headers.get("content-type", "").lower()
        page_text = _decode(raw_bytes, content_type)

        published_at: datetime | None = None
        if "html" in content_type or (not content_type and _looks_like_html(raw_bytes)):
            title, text, published_raw = _extract_html(page_text)
            published_at = parse_date(published_raw)
        else:
            title, text = None, page_text

        raw_char_count = len(text)
        truncated = raw_char_count > self._max_chars
        text = text[: self._max_chars]
        digest = hashlib.sha256(raw_bytes).hexdigest()[:16]

        return FetchedPage(
            url=url,
            final_url=str(response.url),
            title=title,
            text=text,
            status_code=response.status_code,
            content_hash=f"sha256:{digest}",
            byte_count=len(raw_bytes),
            char_count=len(text),
            published_at=published_at,
            retrieved_at=datetime.now(UTC),
            truncated=truncated,
            raw_char_count=raw_char_count,
        )

    async def _get_following_redirects(self, url: str) -> httpx.Response:
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            await _guard_url(current)
            response = await self._get(current)
            if not response.is_redirect:
                return response
            location = response.headers.get("location")
            if not location:
                return response
            current = urljoin(str(response.url), location)
        raise ValueError(f"too many redirects (>{_MAX_REDIRECTS})")

    async def _get(self, url: str) -> httpx.Response:
        async for attempt in async_retrying():
            with attempt:
                response = await self._client.get(url, headers={"User-Agent": _USER_AGENT})
                if not response.is_redirect:
                    _raise_for_status(response)
                return response
        raise RuntimeError("unreachable")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


async def _guard_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parts.scheme!r}")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("missing host")
    if host in _BLOCKED_HOSTS:
        raise ValueError(f"blocked host: {host!r}")

    # Reject obvious IP literals up front (covers the no-resolution path).
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None  # not a literal: resolve via DNS below
    if literal is not None:
        if _is_blocked_ip(literal):
            raise ValueError(f"blocked address: {host!r}")
        return  # valid public literal — no DNS needed

    addresses = await _resolve(host, parts.port)
    if not addresses:
        raise ValueError(f"could not resolve host: {host!r}")
    for addr in addresses:
        if _is_blocked_ip(ipaddress.ip_address(addr)):
            raise ValueError(f"host {host!r} resolves to blocked address {addr!r}")


async def _resolve(host: str, port: int | None) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.run_in_executor(
        None,
        lambda: socket.getaddrinfo(host, port or 0, proto=socket.IPPROTO_TCP),
    )
    return [info[4][0] for info in infos]


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) else None
    if mapped is not None:
        ip = mapped
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
        or ip == _METADATA_IP
        or (isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT)
    )


def _decode(raw: bytes, content_type: str) -> str:
    charset = "utf-8"
    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
    try:
        return raw.decode(charset, errors="ignore")
    except LookupError:
        return raw.decode("utf-8", errors="ignore")


def _looks_like_html(raw: bytes) -> bool:
    head = raw[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def _extract_html(html: str) -> tuple[str | None, str, str | None]:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.title, parser.text, parser.published_raw
