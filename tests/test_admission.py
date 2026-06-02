from dynamic_graph.domain.budget import Caps
from dynamic_graph.domain.graph import GraphEdge, GraphNode, GraphPatch, RunGraph
from dynamic_graph.runtime.admission import admit_patch


def _seed() -> RunGraph:
    return RunGraph(
        nodes={"seed": GraphNode(node_id="seed", kind="seed", objective="q", status="completed")}
    )


def _patch(nodes, edges, **kw) -> GraphPatch:
    return GraphPatch(patch_id="p1", rationale="r", add_nodes=nodes, add_edges=edges, **kw)


def test_admits_nodes_and_edges():
    patch = _patch(
        [GraphNode(node_id="r", kind="research", objective="o")],
        [GraphEdge(source="seed", target="r")],
    )
    new_graph, decision = admit_patch(_seed(), patch, caps=Caps())
    assert decision.admitted
    assert "r" in new_graph.nodes
    assert len(new_graph.edges) == 1
    assert decision.prev_graph_hash != decision.new_graph_hash


def test_rejects_duplicate_node():
    graph = _seed()
    graph.nodes["r"] = GraphNode(node_id="r", kind="research", objective="o")
    patch = _patch([GraphNode(node_id="r", kind="research", objective="other")], [])
    new_graph, decision = admit_patch(graph, patch, caps=Caps())
    assert "r" in decision.rejected_node_ids
    assert new_graph.nodes["r"].objective == "o"


def test_rejects_cycle():
    graph = _seed()
    graph.nodes["a"] = GraphNode(node_id="a", kind="research", objective="o")
    graph.nodes["b"] = GraphNode(node_id="b", kind="quant", objective="o")
    graph.edges = [GraphEdge(source="a", target="b")]
    patch = _patch([], [GraphEdge(source="b", target="a")])
    new_graph, decision = admit_patch(graph, patch, caps=Caps())
    assert ("b", "a") in decision.rejected_edges
    assert len(new_graph.edges) == 1


def test_rejects_over_node_cap():
    patch = _patch([GraphNode(node_id="r", kind="research", objective="o")], [])
    new_graph, decision = admit_patch(_seed(), patch, caps=Caps(max_graph_nodes=1))
    assert "r" in decision.rejected_node_ids
    assert "r" not in new_graph.nodes


def test_failed_or_cancelled_parent_unblocks_child():
    # A child with one failed parent and one completed parent must still become
    # ready (run on what exists) rather than being stranded forever.
    graph = _seed()
    graph.nodes["ok"] = GraphNode(node_id="ok", kind="research", objective="o", status="completed")
    graph.nodes["bad"] = GraphNode(node_id="bad", kind="research", objective="o", status="failed")
    graph.nodes["child"] = GraphNode(node_id="child", kind="quant", objective="o")
    graph.edges = [GraphEdge(source="ok", target="child"), GraphEdge(source="bad", target="child")]
    assert "child" in graph.ready_node_ids()

    # A still-pending parent does keep the child waiting.
    graph.nodes["bad"].status = "pending"
    assert "child" not in graph.ready_node_ids()
