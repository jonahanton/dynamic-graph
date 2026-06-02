from __future__ import annotations

from typing import Annotated, Any, TypedDict

from dynamic_graph.domain.artifacts import Artifact, OpenCrux, ResearchSignal
from dynamic_graph.domain.budget import BudgetState
from dynamic_graph.domain.forecasts import FinalForecast, ForecastEstimate
from dynamic_graph.domain.graph import GraphNode, GraphPatch, PatchDecision, RunGraph


def _extend(left: list | None, right: list | None) -> list:
    return [*(left or []), *(right or [])]


def _keep_latest(left: Any, right: Any) -> Any:
    return right if right is not None else left


class GraphRunState(TypedDict, total=False):
    # The dynamic DAG lives here and is mutated only by admit/reduce (single writers).
    run_graph: RunGraph

    # Accumulating channels (parallel workers + sequential nodes write these).
    artifacts: Annotated[list[Artifact], _extend]
    signals: Annotated[list[ResearchSignal], _extend]
    cruxes: Annotated[list[OpenCrux], _extend]
    estimates: Annotated[list[ForecastEstimate], _extend]
    applied_patches: Annotated[list[GraphPatch], _extend]
    patch_decisions: Annotated[list[PatchDecision], _extend]
    completed: Annotated[list[str], _extend]
    node_errors: Annotated[list[dict], _extend]
    # signal ids the planner has already acted on, so they stop reappearing as open
    addressed_signal_ids: Annotated[list[str], _extend]

    # Last-writer-wins channels (sequential nodes only).
    pending_patch: GraphPatch | None
    final: Annotated[FinalForecast | None, _keep_latest]
    stopping: bool
    stall: int
    budget: BudgetState


def initial_state(seed: GraphNode) -> GraphRunState:
    return {
        "run_graph": RunGraph(nodes={seed.node_id: seed}),
        "artifacts": [],
        "signals": [],
        "cruxes": [],
        "estimates": [],
        "applied_patches": [],
        "patch_decisions": [],
        "completed": [],
        "node_errors": [],
        "addressed_signal_ids": [],
        "pending_patch": None,
        "final": None,
        "stopping": False,
        "stall": 0,
        "budget": BudgetState(),
    }
