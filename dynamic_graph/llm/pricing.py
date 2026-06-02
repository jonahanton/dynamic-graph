from __future__ import annotations

from dataclasses import dataclass
from math import ceil


@dataclass(frozen=True)
class TokenPrices:
    """Prices in micro-dollars per token (i.e. dollars per million tokens)."""

    input: float
    output: float
    cached_input: float = 0.0


# Prices as dollars-per-million-tokens (== micro-dollars per token).
OPENAI_PRICES: dict[str, TokenPrices] = {
    "gpt-5.5": TokenPrices(input=5.00, output=30.00, cached_input=0.50),
    "gpt-5.4": TokenPrices(input=2.50, output=15.00, cached_input=0.25),
    "gpt-5.4-mini": TokenPrices(input=0.75, output=4.50, cached_input=0.075),
}

ANTHROPIC_PRICES: dict[str, TokenPrices] = {
    "claude-opus-4-8": TokenPrices(input=5.00, output=25.00, cached_input=0.50),
    "claude-sonnet-4-6": TokenPrices(input=3.00, output=15.00, cached_input=0.30),
    "claude-haiku-4-5": TokenPrices(input=1.00, output=5.00, cached_input=0.10),
}

_DEFAULT = TokenPrices(input=3.00, output=15.00, cached_input=0.30)


def _prices(provider: str, model: str) -> TokenPrices:
    table = OPENAI_PRICES if provider == "openai" else ANTHROPIC_PRICES
    return table.get(model, _DEFAULT)


def cost_micros(provider: str, model: str, usage: dict[str, int]) -> int:
    """Estimate cost in micro-dollars from raw token usage."""
    prices = _prices(provider, model)
    input_tokens = int(usage.get("input", 0))
    output_tokens = int(usage.get("output", 0))
    cached = int(usage.get("cached_input", 0))
    billable_input = max(input_tokens - cached, 0)
    total = (
        billable_input * prices.input + cached * prices.cached_input + output_tokens * prices.output
    )
    return ceil(total)
