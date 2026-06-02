from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    provider: Literal["brave", "exa"]
    url: str
    title: str
    snippet: str = ""
    published_at: datetime | None = None
    rank: int
    score: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    provider: str
    query: str
    hits: list[SearchHit] = Field(default_factory=list)
    request_id: str | None = None
    null_result: bool = False


class FetchedPage(BaseModel):
    url: str
    final_url: str
    title: str | None = None
    text: str
    status_code: int | None = None
    content_hash: str
    byte_count: int
    char_count: int
    published_at: datetime | None = None
    retrieved_at: datetime
    truncated: bool = False
    raw_char_count: int | None = None


class SearchClient(ABC):
    """A web search provider. Implementations must not log or perform any I/O
    outside `search`; observability and caps are applied by the observed wrapper."""

    provider: str

    @abstractmethod
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
    ) -> SearchResult: ...

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None


class FetchClient(ABC):
    """Fetches and extracts readable text from a single URL."""

    @abstractmethod
    async def fetch(self, url: str) -> FetchedPage: ...

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None
