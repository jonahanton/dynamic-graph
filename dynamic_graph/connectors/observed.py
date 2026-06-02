from __future__ import annotations

from dynamic_graph.connectors.contracts import FetchClient, FetchedPage, SearchClient, SearchResult
from dynamic_graph.observability.runtime import ObservedRuntime


class ObservedWeb:
    """The only way agents reach the web. Every search and fetch is charged
    against caps and enclosed in a Langfuse span mirrored to local JSONL."""

    def __init__(
        self,
        *,
        runtime: ObservedRuntime,
        brave: SearchClient | None = None,
        exa: SearchClient | None = None,
        fetch: FetchClient | None = None,
    ) -> None:
        self._runtime = runtime
        self._search_clients: dict[str, SearchClient] = {}
        if brave is not None:
            self._search_clients["brave"] = brave
        if exa is not None:
            self._search_clients["exa"] = exa
        self._fetch = fetch

    @property
    def providers(self) -> list[str]:
        return list(self._search_clients.keys())

    @property
    def can_fetch(self) -> bool:
        return self._fetch is not None

    def _select(self, provider: str | None) -> SearchClient:
        if provider and provider in self._search_clients:
            return self._search_clients[provider]
        for preferred in ("brave", "exa"):
            if preferred in self._search_clients:
                return self._search_clients[preferred]
        raise RuntimeError("No search provider configured (set BRAVE_API_KEY or EXA_API_KEY)")

    async def search(
        self,
        *,
        actor: str,
        query: str,
        provider: str | None = None,
        count: int = 8,
        freshness: str | None = None,
        end_published_date: str | None = None,
        include_text: bool = False,
        include_highlights: bool = False,
        domains: list[str] | None = None,
    ) -> SearchResult:
        client = self._select(provider)
        self._runtime.charge_search()
        with self._runtime.observe(
            kind="web_search",
            actor=actor,
            name=f"search:{client.provider}",
            input={"query": query, "freshness": freshness, "count": count},
            metadata={"provider": client.provider},
        ) as rec:
            result = await client.search(
                query,
                count=count,
                freshness=freshness,
                end_published_date=end_published_date,
                include_text=include_text,
                include_highlights=include_highlights,
                domains=domains,
            )
            urls = [hit.url for hit in result.hits]
            rec.set_output({"count": len(urls), "urls": urls})
            rec.note(
                summary=f"{client.provider} '{query[:60]}' -> {len(urls)} hits",
                provider=client.provider,
                query=query,
                hit_count=len(urls),
                urls=urls,
                request_id=result.request_id,
                null_result=result.null_result,
            )
            return result

    async def fetch(self, *, actor: str, url: str) -> FetchedPage:
        if self._fetch is None:
            raise RuntimeError("No fetch client configured")
        self._runtime.charge_fetch()
        with self._runtime.observe(
            kind="fetch", actor=actor, name="fetch", input={"url": url}
        ) as rec:
            page = await self._fetch.fetch(url)
            rec.set_output({"final_url": page.final_url, "chars": page.char_count})
            rec.note(
                summary=f"fetch {url[:60]} -> {page.char_count} chars",
                url=url,
                final_url=page.final_url,
                content_hash=page.content_hash,
                byte_count=page.byte_count,
                char_count=page.char_count,
                status_code=page.status_code,
            )
            return page

    async def aclose(self) -> None:
        for client in self._search_clients.values():
            await client.aclose()
        if self._fetch is not None:
            await self._fetch.aclose()
