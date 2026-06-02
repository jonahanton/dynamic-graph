from __future__ import annotations

import json

from dynamic_graph.agents import (
    AgentDeps,
    WorkerResult,
    load_prompt,
    new_id,
    node_brief,
    question_header,
)
from dynamic_graph.agents.schemas import Critique
from dynamic_graph.domain.artifacts import Artifact, OpenCrux, ResearchSignal
from dynamic_graph.domain.graph import GraphNode


def _render(artifacts: list[Artifact]) -> str:
    rows = [
        {"id": a.id, "kind": a.kind, "summary": a.summary[:160], "by": a.created_by}
        for a in artifacts
        if a.kind in ("evidence", "model_card", "simulation", "quant_gap", "source_coverage")
    ]
    return json.dumps(rows, indent=2)[:5000]


async def run(node: GraphNode, deps: AgentDeps, visible_artifacts: list[Artifact]) -> WorkerResult:
    result = WorkerResult()
    actor = node.node_id
    q = deps.question

    critique = await deps.llm.structured(
        prompt_name="critic",
        actor=actor,
        response_model=Critique,
        system=load_prompt("critic"),
        user=(
            question_header(q)
            + node_brief(node)
            + f"\nGathered so far:\n{_render(visible_artifacts)}"
        ),
        max_tokens=1200,
    )

    for crux in critique.cruxes:
        crux_id = new_id("crux")
        result.cruxes.append(
            OpenCrux(
                id=crux_id, created_by=actor, description=crux.description, critical=crux.critical
            )
        )
        result.artifacts.append(
            Artifact(
                id=new_id("cruxart"),
                kind="crux",
                created_by=actor,
                summary=crux.description[:140],
                payload={
                    "crux_id": crux_id,
                    "description": crux.description,
                    "critical": crux.critical,
                },
            )
        )

    for sig in critique.signals:
        result.signals.append(
            ResearchSignal(
                id=new_id("signal"),
                created_by=actor,
                kind=sig.kind,
                description=sig.description,
                suggested_node_kind=None
                if sig.suggested_node_kind == "none"
                else sig.suggested_node_kind,
            )
        )

    return result
