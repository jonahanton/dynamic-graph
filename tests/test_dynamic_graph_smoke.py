import asyncio
import json

from dynamic_graph.examples import hormuz_question
from dynamic_graph.observability import EventLog
from dynamic_graph.runtime import run_forecast
from tests.fakes import offline_deps, offline_runtime


def test_dynamic_graph_runs_and_changes_at_runtime(tmp_path):
    runtime = offline_runtime(tmp_path, hormuz_question())
    deps = offline_deps(runtime)

    final = asyncio.run(run_forecast(deps))
    runtime.shutdown()

    # A forecast was produced and is bounded.
    assert final is not None
    assert 0.0 <= final.probability <= 1.0

    events = EventLog.read(runtime.paths.events)

    # The graph genuinely changed at runtime: at least two admitted patches, and a
    # later one added nodes after the initial planner step, with a hash change.
    patch_events = [e for e in events if e.kind == "graph_patch"]
    admitted = [e for e in patch_events if e.payload.get("admitted")]
    assert len(admitted) >= 2
    assert any(e.payload["prev_graph_hash"] != e.payload["new_graph_hash"] for e in admitted)
    assert any(e.payload.get("accepted_node_ids") for e in admitted[1:])

    # A worker emitted a research signal and the master recorded its handling.
    state = json.loads(runtime.paths.state.read_text())
    assert state["signals"]
    assert any(e.kind == "planner_signals" for e in events)

    # Quant was invoked and registered a typed output (model card or honest gap).
    kinds = {a["kind"] for a in state["artifacts"]}
    assert kinds & {"model_card", "simulation", "quant_gap"}

    # The graph grew a terminal forecast/validate path that completed.
    nodes = state["run_graph"]["nodes"]
    assert "forecast" in nodes and "validate" in nodes
    assert nodes["validate"]["status"] == "completed"
    assert state["final"] is not None

    # Observability captured the required observations.
    assert any(e.kind == "final_forecast" for e in events)
    assert runtime.tracer.generations()  # LLM generations were recorded
    assert any(s.name.startswith("quant_exec") for s in runtime.tracer.spans)
    assert any(s.name.startswith("patch:") for s in runtime.tracer.spans)
    assert any(e.kind == "validation" for e in events)
