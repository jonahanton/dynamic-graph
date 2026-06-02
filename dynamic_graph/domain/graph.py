from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field

NodeKind = Literal["seed", "planner", "research", "quant", "critic", "forecast", "validate"]
# No "running": reduce flips pending -> completed/failed; nothing writes an interim state.
NodeStatus = Literal["pending", "completed", "cancelled", "failed"]


class GraphNode(BaseModel):
    node_id: str
    kind: NodeKind
    objective: str
    # A dynamically written briefing from the master: context and specific
    # instructions for this node, in the spirit of briefing a specialist.
    brief: str = ""
    input_artifact_ids: list[str] = Field(default_factory=list)
    max_calls: int = 3
    # A generous per-node wall-clock backstop enforced by the executor.
    max_runtime_seconds: int = 300
    status: NodeStatus = "pending"
    emitted_artifact_ids: list[str] = Field(default_factory=list)
    error: str | None = None


class GraphEdge(BaseModel):
    source: str
    target: str


class GraphPatch(BaseModel):
    """The only object that can change the run graph. Emitted by the master."""

    patch_id: str
    proposed_by: str = "planner"
    rationale: str
    add_nodes: list[GraphNode] = Field(default_factory=list)
    add_edges: list[GraphEdge] = Field(default_factory=list)
    cancel_node_ids: list[str] = Field(default_factory=list)
    stop: bool = False


class PatchDecision(BaseModel):
    """The admission layer's verdict on a proposed patch."""

    patch_id: str
    admitted: bool
    reason: str = ""
    accepted_node_ids: list[str] = Field(default_factory=list)
    rejected_node_ids: list[str] = Field(default_factory=list)
    rejected_edges: list[tuple[str, str]] = Field(default_factory=list)
    prev_graph_hash: str = ""
    new_graph_hash: str = ""


class RunGraph(BaseModel):
    """The mutable per-run DAG that lives inside graph state."""

    nodes: dict[str, GraphNode] = Field(default_factory=dict)
    edges: list[GraphEdge] = Field(default_factory=list)

    def parents(self, node_id: str) -> list[str]:
        return [e.source for e in self.edges if e.target == node_id]

    def _is_resolved(self, node_id: str) -> bool:
        """A parent stops blocking once terminal: a failed/cancelled parent
        unblocks its children rather than stranding them."""
        node = self.nodes.get(node_id)
        return node is not None and node.status != "pending"

    def ready_node_ids(self) -> list[str]:
        """Pending nodes whose parents have all reached a terminal state."""
        ready: list[str] = []
        for node_id, node in self.nodes.items():
            if node.status != "pending":
                continue
            if all(self._is_resolved(p) for p in self.parents(node_id)):
                ready.append(node_id)
        return ready

    def has_pending_work(self) -> bool:
        return any(n.status == "pending" for n in self.nodes.values())

    def hash(self) -> str:
        payload = {
            "nodes": {nid: [n.kind, n.status] for nid, n in sorted(self.nodes.items())},
            "edges": sorted([[e.source, e.target] for e in self.edges]),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()[:16]
