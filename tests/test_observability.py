import pytest

from dynamic_graph.config.settings import Settings
from dynamic_graph.domain.budget import Caps
from dynamic_graph.examples import hormuz_question
from dynamic_graph.observability import CapExceeded, EventLog
from tests.fakes import offline_runtime


def test_missing_langfuse_credentials_fail():
    settings = Settings(langfuse_public_key="", langfuse_secret_key="", langfuse_host="")
    with pytest.raises(RuntimeError):
        settings.require_langfuse()


def test_observe_writes_span_and_jsonl(tmp_path):
    runtime = offline_runtime(tmp_path, hormuz_question())
    with runtime.observe(kind="demo", actor="actor", name="demo-span") as rec:
        rec.set_output({"ok": True})
        rec.note(summary="did the thing", value=7)
    runtime.shutdown()

    events = EventLog.read(runtime.paths.events)
    matching = [e for e in events if e.kind == "demo"]
    assert matching and matching[0].summary == "did the thing"
    assert matching[0].trace_id and matching[0].observation_id
    assert runtime.tracer.by_name("demo-span")


def test_external_action_requires_active_observation(tmp_path):
    runtime = offline_runtime(tmp_path, hormuz_question())
    with pytest.raises(RuntimeError):
        runtime.charge_llm()  # no active observation
    runtime.shutdown()


def test_caps_block_excess_calls(tmp_path):
    runtime = offline_runtime(tmp_path, hormuz_question(), caps=Caps(max_search_calls=1))
    with runtime.observe(kind="node", actor="n"):
        runtime.charge_search()
        with pytest.raises(CapExceeded):
            runtime.charge_search()
    runtime.shutdown()
