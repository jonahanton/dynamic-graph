from dynamic_graph.agents import master
from dynamic_graph.examples import hormuz_question
from tests.fakes import offline_deps, offline_runtime


def test_fallback_plan_is_question_aware(tmp_path):
    runtime = offline_runtime(tmp_path, hormuz_question())
    deps = offline_deps(runtime)

    # On the first turn the fallback still expands the seed, with question-aware briefs.
    patch, _ = master.fallback_plan(deps, iteration=1, reason="boom", artifacts=[])
    assert {n.kind for n in patch.add_nodes} == {"research", "quant", "critic"}
    title = hormuz_question().title
    assert all(n.brief.strip() for n in patch.add_nodes)
    assert any(title in n.brief for n in patch.add_nodes)  # not generic boilerplate

    # On later turns it stops gracefully rather than expanding blindly.
    _, later = master.fallback_plan(deps, iteration=3, reason="boom", artifacts=[])
    assert later.stop and not later.add_nodes

    runtime.shutdown()
