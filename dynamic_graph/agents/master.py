from __future__ import annotations

import json

from dynamic_graph.agents import AgentDeps, load_prompt, new_id
from dynamic_graph.agents.schemas import PatchNodeSpec, PlannerDecision
from dynamic_graph.domain.artifacts import Artifact, OpenCrux, ResearchSignal
from dynamic_graph.domain.budget import BudgetState, Caps
from dynamic_graph.domain.forecasts import ForecastEstimate
from dynamic_graph.domain.graph import GraphEdge, GraphNode, GraphPatch, RunGraph


def _summary(
    *,
    deps: AgentDeps,
    run_graph: RunGraph,
    artifacts: list[Artifact],
    signals: list[ResearchSignal],
    cruxes: list[OpenCrux],
    estimates: list[ForecastEstimate],
    budget: BudgetState,
    caps: Caps,
    addressed_signal_ids: list[str],
) -> str:
    q = deps.question
    nodes = [
        {"node_id": n.node_id, "kind": n.kind, "status": n.status, "objective": n.objective[:80]}
        for n in run_graph.nodes.values()
    ]
    arts = [
        {"id": a.id, "kind": a.kind, "summary": a.summary[:100], "by": a.created_by}
        for a in artifacts
    ]
    sigs = [
        {
            "id": s.id,
            "kind": s.kind,
            "by": s.created_by,
            "addressed": s.addressed,
            "desc": s.description[:100],
        }
        for s in signals
    ]
    crux_view = [
        {"id": c.id, "critical": c.critical, "status": c.status, "desc": c.description[:100]}
        for c in cruxes
    ]
    payload = {
        "question": q.title,
        "resolution_criteria": q.resolution_criteria,
        "as_of": q.as_of.isoformat(),
        "iteration": budget.iterations,
        "caps": {
            "max_iterations": caps.max_iterations,
            "max_graph_nodes": caps.max_graph_nodes,
            "max_llm_calls": caps.max_llm_calls,
            "max_search_calls": caps.max_search_calls,
        },
        "budget": budget.model_dump(),
        "graph_nodes": nodes,
        "artifacts": arts,
        "open_signals": [
            s for s in sigs if not (s["addressed"] or s["id"] in set(addressed_signal_ids))
        ],
        "open_cruxes": [c for c in crux_view if c["status"] == "open"],
        "estimate_count": len(estimates),
    }
    return json.dumps(payload, indent=2)


def _to_patch(decision: PlannerDecision) -> GraphPatch:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    for spec in decision.add_nodes:
        nodes.append(
            GraphNode(
                node_id=spec.node_id,
                kind=spec.kind,
                objective=spec.objective,
                brief=spec.brief,
                input_artifact_ids=spec.input_artifact_ids,
            )
        )
        for parent in spec.depends_on or ["seed"]:
            edges.append(GraphEdge(source=parent, target=spec.node_id))
    return GraphPatch(
        patch_id=new_id("patch"),
        proposed_by="planner",
        rationale=decision.rationale,
        add_nodes=nodes,
        add_edges=edges,
        cancel_node_ids=decision.cancel_node_ids,
        stop=decision.stop,
    )


def fallback_plan(
    deps: AgentDeps, *, iteration: int, reason: str, artifacts: list[Artifact]
) -> tuple[GraphPatch, PlannerDecision]:
    """Keep the run alive when the planner LLM call fails: expand the seed on the
    first turn, otherwise stop and finalise on whatever evidence exists."""
    if iteration <= 1:
        decision = _default_expansion(deps, artifacts)
        decision.rationale = f"planner unavailable ({reason}); using default seed expansion"
    else:
        decision = PlannerDecision(rationale=f"planner unavailable ({reason}); stopping", stop=True)
    return _to_patch(decision), decision


def _default_expansion(deps: AgentDeps, artifacts: list[Artifact]) -> PlannerDecision:
    """Question-aware seed expansion used only when the planner LLM yields nothing."""
    q = deps.question
    as_of = q.as_of.date().isoformat()
    rule = q.resolution_criteria[:200]
    known = [a.id for a in artifacts][:8]
    seen = f" (so far: {known})" if known else ""
    return PlannerDecision(
        rationale="Default seed expansion: research, quant and critic over the question.",
        add_nodes=[
            PatchNodeSpec(
                node_id="research-current",
                kind="research",
                objective="Find current-state evidence and the resolution source.",
                brief=(
                    f"Establish the current state relevant to '{q.title}' as of {as_of}, and pin "
                    f"down exactly how it resolves under: {rule}. Prioritise official/primary "
                    "sources and the specific numeric series the question turns on; report the "
                    "latest known value and its date."
                ),
                depends_on=["seed"],
            ),
            PatchNodeSpec(
                node_id="quant-base",
                kind="quant",
                objective="Model the resolution probability.",
                brief=(
                    f"Using the research artefacts{seen}, build the simplest adequate model of "
                    f"P(YES) for '{q.title}' against the rule: {rule} (base rate, event-time, or a "
                    "short simulation). If the data is too weak, return an honest gap naming the "
                    "exact missing series/source."
                ),
                depends_on=["research-current"],
            ),
            PatchNodeSpec(
                node_id="critic",
                kind="critic",
                objective="Surface the decisive cruxes.",
                brief=(
                    f"Review the evidence and quant output on '{q.title}' and surface the few "
                    "cruxes most likely to move the forecast, plus any point-in-time leakage "
                    f"(sources dated after {as_of}) or fragile-assumption concerns."
                ),
                depends_on=["research-current", "quant-base"],
            ),
        ],
    )


async def plan(
    deps: AgentDeps,
    *,
    run_graph: RunGraph,
    artifacts: list[Artifact],
    signals: list[ResearchSignal],
    cruxes: list[OpenCrux],
    estimates: list[ForecastEstimate],
    budget: BudgetState,
    caps: Caps,
    addressed_signal_ids: list[str] | None = None,
) -> tuple[GraphPatch, PlannerDecision]:
    user = _summary(
        deps=deps,
        run_graph=run_graph,
        artifacts=artifacts,
        signals=signals,
        cruxes=cruxes,
        estimates=estimates,
        budget=budget,
        caps=caps,
        addressed_signal_ids=addressed_signal_ids or [],
    )
    decision = await deps.llm.structured(
        prompt_name="master",
        actor="planner",
        response_model=PlannerDecision,
        system=load_prompt("master"),
        user=user,
        max_tokens=1400,
    )
    if budget.iterations <= 1 and not decision.add_nodes and not decision.stop:
        decision = _default_expansion(deps, artifacts)
        deps.runtime.emit(
            "planner_fallback",
            "planner",
            "planner returned no nodes; applied dynamic default expansion",
            nodes=[n.node_id for n in decision.add_nodes],
        )
    return _to_patch(decision), decision
