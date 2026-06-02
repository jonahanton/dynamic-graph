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
from dynamic_graph.agents.schemas import ForecastOutput
from dynamic_graph.domain.artifacts import Artifact, OpenCrux
from dynamic_graph.domain.forecasts import ForecastEstimate
from dynamic_graph.domain.graph import GraphNode


def _has_lineage(a: Artifact) -> bool:
    """A quant output is trustworthy only if it carries diagnostics and the
    generated-code lineage that proves it was actually executed."""
    p = a.payload
    return all(bool(p.get(k)) for k in ("diagnostics", "code_hashes", "package_versions"))


def _render(
    artifacts: list[Artifact],
    trusted_models: list[Artifact],
    excluded_model_ids: list[str],
    cruxes: list[OpenCrux],
) -> str:
    evidence = [
        {
            "claim": a.payload.get("claim"),
            "stance": a.payload.get("stance"),
            "strength": a.payload.get("strength"),
        }
        for a in artifacts
        if a.kind == "evidence"
    ]
    models = [
        {
            "id": a.id,
            "method": a.payload.get("method"),
            "headline_probability": a.payload.get("headline_probability"),
            "interval": [a.payload.get("interval_low"), a.payload.get("interval_high")],
            "findings": (a.payload.get("findings") or "")[:300],
            "confidence": a.payload.get("confidence"),
            "estimates": [
                {"name": e.get("name"), "value": e.get("value")}
                for e in a.payload.get("estimates", [])
            ],
            "contribution": a.payload.get("forecast_contribution"),
            "diagnostics": [d.get("status") for d in a.payload.get("diagnostics", [])],
        }
        for a in trusted_models
    ]
    gaps = [a.payload.get("missing") for a in artifacts if a.kind == "quant_gap"]
    open_cruxes = [
        {"desc": c.description, "critical": c.critical} for c in cruxes if c.status == "open"
    ]
    return json.dumps(
        {
            "evidence": evidence,
            "models": models,
            "excluded_models": excluded_model_ids,  # untrustworthy: no diagnostics/lineage
            "quant_gaps": gaps,
            "open_cruxes": open_cruxes,
        },
        indent=2,
    )[:6000]


async def run(
    node: GraphNode,
    deps: AgentDeps,
    visible_artifacts: list[Artifact],
    cruxes: list[OpenCrux],
) -> WorkerResult:
    result = WorkerResult()
    actor = node.node_id
    q = deps.question

    all_models = [a for a in visible_artifacts if a.kind in ("model_card", "simulation")]
    trusted_models = [a for a in all_models if _has_lineage(a)]
    trusted_ids = {a.id for a in trusted_models}
    excluded_ids = [a.id for a in all_models if a.id not in trusted_ids]

    output = await deps.llm.structured(
        prompt_name="forecast",
        actor=actor,
        response_model=ForecastOutput,
        system=load_prompt("forecast"),
        user=(
            question_header(q)
            + node_brief(node)
            + "\nEvidence, models and cruxes:\n"
            + _render(visible_artifacts, trusted_models, excluded_ids, cruxes)
        ),
        max_tokens=900,
    )

    probability = min(max(output.probability, 0.0), 1.0)
    # Only trustworthy cards may be recorded as used; never an excluded one.
    used = [cid for cid in output.used_model_card_ids if cid in trusted_ids]
    model_card_ids = used or sorted(trusted_ids)
    source_ids = [a.id for a in visible_artifacts if a.kind in ("source", "evidence")]

    estimate = ForecastEstimate(
        id=new_id("estimate"),
        created_by=actor,
        probability=probability,
        rationale=output.rationale,
        method="blind",
        source_ids=source_ids,
        model_card_ids=model_card_ids,
        trace_id=deps.runtime.tracer.current_trace_id(),
    )
    result.estimates.append(estimate)
    result.artifacts.append(
        Artifact(
            id=new_id("estart"),
            kind="estimate",
            created_by=actor,
            summary=f"p={probability:.2f}",
            payload={
                "probability": probability,
                "rationale": output.rationale,
                "key_drivers": output.key_drivers,
            },
            source_ids=source_ids,
        )
    )
    return result
