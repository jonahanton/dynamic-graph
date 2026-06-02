from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed environment settings. Reads `.env` then process environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    model_provider: Literal["openai", "anthropic"] = "anthropic"
    openai_model: str = "gpt-5.4-mini"
    anthropic_model: str = "claude-sonnet-4-6"

    openai_api_key: str = ""
    anthropic_api_key: str = ""

    brave_api_key: str = ""
    exa_api_key: str = ""

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    def require_langfuse(self) -> None:
        missing = [
            name
            for name, value in (
                ("LANGFUSE_PUBLIC_KEY", self.langfuse_public_key),
                ("LANGFUSE_SECRET_KEY", self.langfuse_secret_key),
                ("LANGFUSE_HOST", self.langfuse_host),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Langfuse is mandatory for real runs. Missing: "
                + ", ".join(missing)
                + ". Set them in .env before running."
            )

    def require_model_key(self) -> None:
        key = self.openai_api_key if self.model_provider == "openai" else self.anthropic_api_key
        if not key:
            raise RuntimeError(
                f"Missing API key for model provider '{self.model_provider}'. "
                "Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env."
            )

    def require_search(self) -> None:
        if not (self.brave_api_key or self.exa_api_key):
            raise RuntimeError(
                "At least one web search key is required. Set BRAVE_API_KEY or EXA_API_KEY in .env."
            )


def load_settings() -> Settings:
    return Settings()
