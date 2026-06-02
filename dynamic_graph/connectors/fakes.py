from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal

from .contracts import FetchClient, FetchedPage, SearchClient, SearchHit, SearchResult


def _default_hits(provider: Literal["brave", "exa"]) -> list[SearchHit]:
    now = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)
    return [
        SearchHit(
            provider=provider,
            url="https://www.reuters.com/world/example-article-2026",
            title="Example headline on the topic",
            snippet="A concise summary of the first source covering the query.",
            published_at=now,
            rank=1,
            score=0.91 if provider == "exa" else None,
        ),
        SearchHit(
            provider=provider,
            url="https://en.wikipedia.org/wiki/Example_topic",
            title="Example topic - Wikipedia",
            snippet="Background and context for the query from an encyclopaedic source.",
            published_at=datetime(2026, 4, 1, tzinfo=UTC),
            rank=2,
            score=0.84 if provider == "exa" else None,
        ),
        SearchHit(
            provider=provider,
            url="https://www.economist.com/example-analysis",
            title="Analysis: what the query means",
            snippet="An analytical perspective relevant to the search query.",
            published_at=datetime(2026, 5, 10, tzinfo=UTC),
            rank=3,
            score=0.78 if provider == "exa" else None,
        ),
    ]


class FakeSearchClient(SearchClient):
    """Deterministic in-memory search client for tests. No network."""

    def __init__(
        self,
        provider: Literal["brave", "exa"] = "brave",
        results: dict[str, list[SearchHit]] | None = None,
        default_hits: list[SearchHit] | None = None,
    ) -> None:
        self.provider = provider
        self._results = results or {}
        self._default_hits = default_hits if default_hits is not None else _default_hits(provider)
        self.calls: list[str] = []

    async def search(
        self,
        query: str,
        *,
        count: int = 8,
        freshness: str | None = None,
        end_published_date: str | None = None,
        include_text: bool = False,
        include_highlights: bool = False,
        domains: list[str] | None = None,
    ) -> SearchResult:
        self.calls.append(query)
        hits = self._results.get(query, self._default_hits)[:count]
        return SearchResult(
            provider=self.provider,
            query=query,
            hits=hits,
            request_id=f"fake-{self.provider}-{len(self.calls)}",
            null_result=not hits,
        )


class FakeFetchClient(FetchClient):
    """Deterministic in-memory fetch client for tests. No network."""

    _DEFAULT_TEXT = (
        "This is a canned page body returned by the fake fetch client. "
        "It contains a couple of sentences of plausible readable text."
    )

    def __init__(self, pages: dict[str, str] | None = None) -> None:
        self._pages = pages or {}
        self.calls: list[str] = []

    async def fetch(self, url: str) -> FetchedPage:
        self.calls.append(url)
        text = self._pages.get(url, self._DEFAULT_TEXT)
        raw_bytes = text.encode("utf-8")
        digest = hashlib.sha256(raw_bytes).hexdigest()[:16]
        return FetchedPage(
            url=url,
            final_url=url,
            title="Fake page",
            text=text,
            status_code=200,
            content_hash=f"sha256:{digest}",
            byte_count=len(raw_bytes),
            char_count=len(text),
            published_at=None,
            retrieved_at=datetime.now(UTC),
        )
