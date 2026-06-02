from __future__ import annotations

from typing import Any

from dynamic_graph.domain.artifacts import Artifact
from dynamic_graph.domain.budget import Caps
from dynamic_graph.domain.graph import GraphNode, RunGraph


def visible_artifacts(node: GraphNode, artifacts: list[Artifact]) -> list[Artifact]:
    """Artifacts a node can see: its declared inputs if any resolve, else all."""
    if node.input_artifact_ids:
        ids = set(node.input_artifact_ids)
        scoped = [a for a in artifacts if a.id in ids]
        if scoped:
            return scoped
    return list(artifacts)


def ready_wave(run_graph: RunGraph, caps: Caps) -> list[str]:
    return run_graph.ready_node_ids()[: caps.max_wave_workers]


def send_payload(state: dict[str, Any], node_id: str) -> dict[str, Any]:
    run_graph: RunGraph = state["run_graph"]
    node = run_graph.nodes[node_id]
    artifacts = state.get("artifacts", [])
    return {
        "node_id": node_id,
        "node": node,
        "visible_artifacts": visible_artifacts(node, artifacts),
        "estimates": state.get("estimates", []),
        "cruxes": state.get("cruxes", []),
    }
