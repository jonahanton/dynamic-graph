from __future__ import annotations

import json
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from dynamic_graph.llm.client import LLMClient, LLMResponse

_API_URL = "https://api.anthropic.com/v1/messages"
_TRANSIENT_STATUS = (429, 500, 502, 503, 504)


class AnthropicClient(LLMClient):
    provider = "anthropic"

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
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        response = await self._client.post(_API_URL, headers=headers, json=body)
        if response.status_code >= 400 and response.status_code not in _TRANSIENT_STATUS:
            raise RuntimeError(f"Anthropic error {response.status_code}: {response.text[:400]}")
        response.raise_for_status()  # transient statuses raise -> tenacity retries
        return response.json()

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
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": user}],
            "tools": [
                {
                    "name": schema_name,
                    "description": "Return the requested structured output.",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": schema_name},
        }
        if system:
            body["system"] = system

        data = await self._post(body)
        content = data.get("content", [])

        tool_input: dict[str, Any] | None = None
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        for block in content:
            btype = block.get("type")
            if btype == "tool_use" and block.get("name") == schema_name:
                tool_input = block.get("input")
            elif btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "thinking":
                thinking_parts.append(block.get("thinking", ""))

        if tool_input is None:
            # fallback: a model that ignored the tool may have emitted JSON text
            joined = "".join(text_parts).strip()
            try:
                tool_input = json.loads(joined)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Anthropic returned no structured output: {joined[:300]}"
                ) from exc

        usage = data.get("usage", {})
        return LLMResponse(
            data=tool_input,
            text=json.dumps(tool_input),
            model=self.model,
            provider=self.provider,
            usage={
                "input": int(usage.get("input_tokens", 0)),
                "output": int(usage.get("output_tokens", 0)),
                "cached_input": int(usage.get("cache_read_input_tokens", 0)),
            },
            response_id=data.get("id"),
            tool_calls=[{"name": schema_name, "input": tool_input}],
            reasoning="\n".join(p for p in thinking_parts if p) or None,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
