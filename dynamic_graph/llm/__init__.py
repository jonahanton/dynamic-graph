from __future__ import annotations

import httpx

from dynamic_graph.config.settings import Settings
from dynamic_graph.llm.anthropic import AnthropicClient
from dynamic_graph.llm.client import LLMClient, LLMResponse, harden_schema
from dynamic_graph.llm.observed import ObservedLLM
from dynamic_graph.llm.openai import OpenAIClient


def build_llm(settings: Settings, *, client: httpx.AsyncClient | None = None) -> LLMClient:
    if settings.model_provider == "openai":
        return OpenAIClient(settings.openai_api_key, settings.openai_model, client=client)
    return AnthropicClient(settings.anthropic_api_key, settings.anthropic_model, client=client)


__all__ = [
    "AnthropicClient",
    "OpenAIClient",
    "LLMClient",
    "LLMResponse",
    "ObservedLLM",
    "build_llm",
    "harden_schema",
]
