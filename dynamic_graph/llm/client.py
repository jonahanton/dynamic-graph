from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResponse:
    data: dict[str, Any]
    text: str
    model: str
    provider: str
    usage: dict[str, int] = field(default_factory=dict)
    response_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    reasoning: str | None = None
    cost_micros: int = 0


class LLMClient(ABC):
    """A structured-output LLM client. Returns validated-against-schema JSON."""

    provider: str
    model: str

    @abstractmethod
    async def complete(
        self,
        *,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        system: str | None = None,
        max_tokens: int = 900,
        temperature: float = 0.0,
    ) -> LLMResponse: ...

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None


def harden_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a JSON schema acceptable to strict structured-output APIs: every
    object forbids extra properties and lists all properties as required."""

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return schema
