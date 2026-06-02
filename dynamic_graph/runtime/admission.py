from __future__ import annotations

from dynamic_graph.domain.budget import Caps
from dynamic_graph.domain.graph import GraphPatch, PatchDecision, RunGraph


def _reaches(graph: RunGraph, start: str, goal: str) -> bool:
    """True if `goal` is reachable from `start` along directed edges (start==goal counts)."""
    if start == goal:
        return True
    adjacency: dict[str, list[str]] = {}
    for edge in graph.edges:
        adjacency.setdefault(edge.source, []).append(edge.target)
    seen: set[str] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(adjacency.get(node, []))
    return False


def admit_patch(
    run_graph: RunGraph, patch: GraphPatch, *, caps: Caps
) -> tuple[RunGraph, PatchDecision]:
    """Validate a master patch against caps, duplicates and cycles, then apply the
    accepted parts to a copy of the run graph. Workers never reach this layer."""
    prev_hash = run_graph.hash()
    new_graph = run_graph.model_copy(deep=True)
    accepted_nodes: list[str] = []
    rejected_nodes: list[str] = []
    rejected_edges: list[tuple[str, str]] = []
    reasons: list[str] = []

    for node_id in patch.cancel_node_ids:
        node = new_graph.nodes.get(node_id)
        if node and node.status == "pending":
            node.status = "cancelled"

    for node in patch.add_nodes:
        if node.node_id in new_graph.nodes:
            rejected_nodes.append(node.node_id)
            reasons.append(f"duplicate node {node.node_id}")
            continue
        if len(new_graph.nodes) >= caps.max_graph_nodes:
            rejected_nodes.append(node.node_id)
            reasons.append("max_graph_nodes reached")
            continue
        new_graph.nodes[node.node_id] = node.model_copy()
        accepted_nodes.append(node.node_id)

    existing_edges = {(e.source, e.target) for e in new_graph.edges}
    for edge in patch.add_edges:
        pair = (edge.source, edge.target)
        if edge.source not in new_graph.nodes or edge.target not in new_graph.nodes:
            rejected_edges.append(pair)
            reasons.append(f"edge endpoint missing {pair}")
            continue
        if pair in existing_edges:
            continue
        if len(new_graph.edges) >= caps.max_graph_edges:
            rejected_edges.append(pair)
            reasons.append("max_graph_edges reached")
            continue
        if _reaches(new_graph, edge.target, edge.source):
            rejected_edges.append(pair)
            reasons.append(f"edge {pair} would create a cycle")
            continue
        new_graph.edges.append(edge)
        existing_edges.add(pair)

    admitted = bool(accepted_nodes) or bool(patch.cancel_node_ids) or patch.stop
    decision = PatchDecision(
        patch_id=patch.patch_id,
        admitted=admitted,
        reason="; ".join(reasons),
        accepted_node_ids=accepted_nodes,
        rejected_node_ids=rejected_nodes,
        rejected_edges=rejected_edges,
        prev_graph_hash=prev_hash,
        new_graph_hash=new_graph.hash(),
    )
    return new_graph, decision
