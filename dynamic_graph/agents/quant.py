from __future__ import annotations

import json

from dynamic_graph.agents import AgentDeps, WorkerResult, load_prompt, new_id, node_brief
from dynamic_graph.agents.schemas import QuantPlan, QuantReport
from dynamic_graph.domain.artifacts import Artifact, ResearchSignal
from dynamic_graph.domain.graph import GraphNode
from dynamic_graph.quant import QuantExecutionResult
from dynamic_graph.quant.context import QuantContext
from dynamic_graph.quant.outputs import (
    DiagnosticCheck,
    ModelCard,
    QuantEstimate,
    QuantGap,
    to_artifact,
)


def _plan_user(ctx: QuantContext, node: GraphNode) -> str:
    return (
        f"Question: {ctx.question_title}\nResolution: {ctx.resolution_criteria}\n"
        f"as_of: {ctx.as_of}\n{node_brief(node)}"
        f"Workspace dir (cwd at runtime): {ctx.workspace_dir}\n"
        f"Importable packages: {json.dumps(ctx.available_packages)}\n"
        f"Budget: keep datasets under ~{ctx.max_rows} rows and total outputs under "
        f"{ctx.max_output_bytes} bytes; the script is killed after {ctx.max_runtime_seconds}s "
        "with no network access.\n"
        f"Input artefacts:\n{json.dumps(ctx.input_artifacts, indent=2)[:4000]}\n\n"
        "Step 1: decide model vs gap and, if modelling, write a complete analysis.py."
    )


def _report_user(
    ctx: QuantContext, node: GraphNode, plan: QuantPlan, ex: QuantExecutionResult
) -> str:
    return (
        f"Question: {ctx.question_title}\nNode objective: {node.objective}\n"
        f"Approach: {plan.approach}\n\n"
        f"--- analysis.py ---\n{plan.analysis_code[:2500]}\n\n"
        f"--- execution ---\nok={ex.ok} exit={ex.exit_code} timed_out={ex.timed_out} "
        f"duration={ex.duration_seconds}s error={ex.error}\n"
        f"stdout:\n{ex.stdout[-2500:]}\nstderr:\n{ex.stderr[-1200:]}\n"
        f"output files: {[o.filename for o in ex.output_files]}\n\n"
        "Step 2: produce a model card or simulation artefact (with diagnostics and a "
        "sensitivity check) or an honest gap."
    )


def _priors_dict(priors: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in priors:
        key, _, value = item.partition("=")
        out[key.strip() or item] = value.strip()
    return out


def _augment(
    report: QuantReport, ex: QuantExecutionResult
) -> tuple[list[DiagnosticCheck], list[DiagnosticCheck]]:
    diagnostics = list(report.diagnostics)
    diagnostics.append(
        DiagnosticCheck(
            name="execution",
            status="pass" if ex.ok else "fail",
            detail=f"exit={ex.exit_code}, {len(ex.output_files)} outputs, {ex.duration_seconds}s",
        )
    )
    pe = report.headline_probability
    diagnostics.append(
        DiagnosticCheck(
            name="probability_bounds",
            status="pass" if (pe is None or 0.0 <= pe <= 1.0) else "fail",
            detail=f"headline_probability={pe}",
            observed=pe,
        )
    )
    sensitivity = list(report.sensitivity)
    if not sensitivity:
        sensitivity.append(
            DiagnosticCheck(
                name="sensitivity",
                status="warn",
                detail="No explicit sensitivity sweep reported by the analysis.",
            )
        )
    return diagnostics, sensitivity


async def run(node: GraphNode, deps: AgentDeps, visible_artifacts: list[Artifact]) -> WorkerResult:
    result = WorkerResult()
    actor = node.node_id
    q = deps.question
    workspace = deps.quant.workspace(node.node_id)
    ctx = deps.quant.context(workspace, visible_artifacts)

    plan = await deps.llm.structured(
        prompt_name="quant_plan",
        actor=actor,
        response_model=QuantPlan,
        system=load_prompt("quant"),
        user=_plan_user(ctx, node),
        max_tokens=2200,
    )

    if plan.decision == "gap" or not plan.analysis_code.strip():
        gap = QuantGap(
            gap_id=new_id("gap"),
            created_by=actor,
            missing=plan.gap_missing or "insufficient data to model this question",
            needed_for=plan.gap_needed_for or node.objective,
            suggested_search=plan.gap_suggested_search,
        )
        result.artifacts.append(to_artifact(gap))
        result.signals.append(
            ResearchSignal(
                id=new_id("signal"),
                created_by=actor,
                kind="follow_up_search" if gap.suggested_search else "missing_data",
                description=gap.suggested_search or gap.missing,
                suggested_node_kind="research",
            )
        )
        return result

    deps.quant.write_analysis(actor=actor, workspace=workspace, code=plan.analysis_code)
    ex = await deps.quant.execute(actor=actor, workspace=workspace)

    report = await deps.llm.structured(
        prompt_name="quant_report",
        actor=actor,
        response_model=QuantReport,
        system=load_prompt("quant"),
        user=_report_user(ctx, node, plan, ex),
        max_tokens=1500,
    )

    source_ids = [a.id for a in visible_artifacts if a.kind in ("source", "evidence", "dataset")]

    if report.is_gap or not ex.ok:
        gap = QuantGap(
            gap_id=new_id("gap"),
            created_by=actor,
            missing=report.gap_missing or (ex.error or "model run was not trustworthy"),
            needed_for=report.gap_needed_for or node.objective,
        )
        result.artifacts.append(to_artifact(gap))
        result.signals.append(
            ResearchSignal(
                id=new_id("signal"),
                created_by=actor,
                kind="missing_data",
                description=gap.missing,
                suggested_node_kind="research",
            )
        )
        return result

    diagnostics, sensitivity = _augment(report, ex)
    card = ModelCard(
        model_card_id=new_id("model"),
        created_by=actor,
        target_estimand=report.target_estimand or node.objective,
        method=report.method or plan.approach or "unspecified",
        findings=report.findings,
        confidence=report.confidence,
        headline_probability=report.headline_probability,
        interval_low=report.interval_low,
        interval_high=report.interval_high,
        estimates=[QuantEstimate(**e.model_dump()) for e in report.estimates],
        assumptions=report.assumptions,
        priors=_priors_dict(report.priors),
        diagnostics=diagnostics,
        sensitivity=sensitivity,
        failure_modes=report.failure_modes,
        forecast_contribution=report.forecast_contribution,
        n_draws=report.n_draws,
        source_ids=source_ids,
        input_dataset_ids=[o.dataset_id for o in ex.output_files],
        data_cutoff=q.as_of.isoformat(),
        as_of=q.as_of.isoformat(),
        code_paths=[ex.code_path],
        code_hashes=[ex.code_hash],
        output_paths=[o.path for o in ex.output_files],
        package_versions=ex.package_versions,
        execution_summary={
            "exit_code": ex.exit_code,
            "duration_s": ex.duration_seconds,
            "stdout_tail": ex.stdout[-800:],
        },
        trace_id=ex.trace_id,
        observation_id=ex.observation_id,
    )
    result.artifacts.append(to_artifact(card))
    return result
