from __future__ import annotations

from pydantic import BaseModel


class Caps(BaseModel):
    """Hard limits that keep the dynamic graph from looping or exploding cost."""

    max_iterations: int = 6
    max_graph_nodes: int = 24
    max_graph_edges: int = 48
    max_wave_workers: int = 6
    max_llm_calls: int = 40
    max_search_calls: int = 20
    max_fetch_calls: int = 24
    max_quant_executions: int = 6
    max_quant_rows: int = 200_000
    max_quant_bytes: int = 20_000_000
    max_quant_runtime_seconds: int = 60

    @classmethod
    def small(cls) -> Caps:
        """Tight caps for a tiny acceptance smoke run."""
        return cls(
            max_iterations=3,
            max_graph_nodes=12,
            max_graph_edges=24,
            max_wave_workers=4,
            max_llm_calls=16,
            max_search_calls=6,
            max_fetch_calls=6,
            max_quant_executions=2,
            max_quant_runtime_seconds=45,
        )


class BudgetState(BaseModel):
    """A snapshot of consumption, mirrored from the live runtime into graph state."""

    iterations: int = 0
    llm_calls: int = 0
    search_calls: int = 0
    fetch_calls: int = 0
    quant_executions: int = 0
    cost_micros: int = 0
