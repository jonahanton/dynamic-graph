from __future__ import annotations

import json
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from dynamic_graph.llm.client import LLMClient, LLMResponse

_API_URL = "https://api.openai.com/v1/responses"
_TRANSIENT_STATUS = (429, 500, 502, 503, 504)
_MAX_RETRY_TOKENS = 8000


class OpenAIClient(LLMClient):
    provider = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=8),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        response = await self._client.post(_API_URL, headers=headers, json=body)
        if response.status_code >= 400 and response.status_code not in _TRANSIENT_STATUS:
            raise RuntimeError(f"OpenAI error {response.status_code}: {response.text[:400]}")
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        if data.get("output_text"):
            return data["output_text"]
        parts: list[str] = []
        for item in data.get("output", []):
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    parts.append(block.get("text", ""))
        return "".join(parts)

    @staticmethod
    def _truncated(data: dict[str, Any]) -> bool:
        """The model hit its output-token budget, so the JSON is likely cut off."""
        return data.get("status") == "incomplete" and (
            (data.get("incomplete_details") or {}).get("reason") == "max_output_tokens"
        )

    async def complete(
        self,
        *,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        system: str | None = None,
        max_tokens: int = 900,
        temperature: float = 0.0,
    ) -> LLMResponse:
        budget = max_tokens
        text = ""
        # A truncated response yields invalid JSON; widen the budget once and retry.
        for attempt in range(2):
            body: dict[str, Any] = {
                "model": self.model,
                "input": user,
                "max_output_tokens": budget,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    }
                },
            }
            if system:
                body["instructions"] = system

            data = await self._post(body)
            text = self._extract_text(data).strip()
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                if attempt == 0 and self._truncated(data):
                    budget = min(budget * 3, _MAX_RETRY_TOKENS)
                    continue
                raise RuntimeError(f"OpenAI returned non-JSON output: {text[:300]}") from exc

            usage = data.get("usage", {})
            cached = usage.get("input_tokens_details", {}).get("cached_tokens", 0)
            return LLMResponse(
                data=parsed,
                text=text,
                model=self.model,
                provider=self.provider,
                usage={
                    "input": int(usage.get("input_tokens", 0)),
                    "output": int(usage.get("output_tokens", 0)),
                    "cached_input": int(cached),
                },
                response_id=data.get("id"),
            )
        raise RuntimeError(f"OpenAI returned non-JSON output after retry: {text[:300]}")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
