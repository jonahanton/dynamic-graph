from __future__ import annotations

import asyncio
import json
import math
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from dynamic_graph.agents import AgentDeps, WorkerResult, new_id
from dynamic_graph.agents import critic as critic_agent
from dynamic_graph.agents import forecaster as forecaster_agent
from dynamic_graph.agents import master as master_agent
from dynamic_graph.agents import quant as quant_agent
from dynamic_graph.agents import research as research_agent
from dynamic_graph.domain.artifacts import Artifact
from dynamic_graph.domain.forecasts import FinalForecast
from dynamic_graph.domain.graph import GraphNode, RunGraph
from dynamic_graph.observability.runtime import CapExceeded, ObservedRuntime, Recorder
from dynamic_graph.runtime.admission import admit_patch
from dynamic_graph.runtime.reducers import GraphRunState, initial_state
from dynamic_graph.runtime.scheduler import ready_wave, send_payload
from dynamic_graph.validation.final import finalise_forecast


def _run_validate(
    node: GraphNode,
    deps: AgentDeps,
    visible: list[Artifact],
    estimates: list,
    cruxes: list,
    rec: Recorder,
) -> WorkerResult:
    final, report = finalise_forecast(
        question=deps.question, estimates=estimates, artifacts=visible, cruxes=cruxes
    )
    rec.note(
        verdict=report.verdict,
        checks=[c.model_dump() for c in report.checks],
        probability=final.probability,
    )
    artifact = Artifact(
        id=new_id("final"),
        kind="final",
        created_by=node.node_id,
        summary=f"final p={final.probability:.2f} ({report.verdict})",
        payload={**final.model_dump(mode="json"), "validation": report.model_dump()},
        source_ids=final.source_ids,
    )
    return WorkerResult(final=final, artifacts=[artifact])


def _persist(runtime: ObservedRuntime, state: dict[str, Any], final: FinalForecast | None) -> None:
    run_graph: RunGraph = state["run_graph"]
    snapshot = {
        "run_id": runtime.run_id,
        "question": runtime.question.model_dump(mode="json"),
        "budget": runtime.snapshot().model_dump(),
        "run_graph": run_graph.model_dump(mode="json"),
        "graph_hash": run_graph.hash(),
        "patch_decisions": [d.model_dump() for d in state.get("patch_decisions", [])],
        "artifacts": [
            {"id": a.id, "kind": a.kind, "by": a.created_by, "summary": a.summary}
            for a in state.get("artifacts", [])
        ],
        "signals": [s.model_dump(mode="json") for s in state.get("signals", [])],
        "cruxes": [c.model_dump(mode="json") for c in state.get("cruxes", [])],
        "final": final.model_dump(mode="json") if final else None,
    }
    runtime.paths.state.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")


def build_app(deps: AgentDeps):
    runtime = deps.runtime
    caps = runtime.caps

    async def plan_node(state: GraphRunState) -> dict[str, Any]:
        iteration = runtime.bump_iteration()
        already_addressed = state.get("addressed_signal_ids", [])
        open_signal_ids = [
            s.id for s in state["signals"] if not (s.addressed or s.id in set(already_addressed))
        ]
        # Enclosing observation so the planner's LLM call runs inside an active span.
        with runtime.observe(
            kind="planner_signals",
            actor="planner",
            name=f"plan:iter{iteration}",
            input={"iteration": iteration, "open_signals": open_signal_ids},
        ) as rec:
            try:
                patch, decision = await master_agent.plan(
                    deps,
                    run_graph=state["run_graph"],
                    artifacts=state["artifacts"],
                    signals=state["signals"],
                    cruxes=state["cruxes"],
                    estimates=state["estimates"],
                    budget=runtime.snapshot(),
                    caps=caps,
                    addressed_signal_ids=already_addressed,
                )
            except Exception as exc:  # noqa: BLE001 - planner failure must not abort the run
                patch, decision = master_agent.fallback_plan(
                    deps,
                    iteration=iteration,
                    reason=f"{type(exc).__name__}: {exc}",
                    artifacts=state["artifacts"],
                )
                rec.annotate(planner_error=f"{type(exc).__name__}: {exc}")
            rec.set_output(
                {
                    "addressed_signal_ids": decision.addressed_signal_ids,
                    "add_nodes": [n.node_id for n in patch.add_nodes],
                    "stop": patch.stop,
                }
            )
            rec.note(
                summary=(
                    f"addressed {decision.addressed_signal_ids or 'none'}; "
                    f"+{[n.node_id for n in patch.add_nodes]} stop={patch.stop}"
                ),
                addressed_signal_ids=decision.addressed_signal_ids,
                ignored_signals_note=decision.ignored_signals_note,
                add_nodes=[n.node_id for n in patch.add_nodes],
                stop=patch.stop,
            )
        return {
            "pending_patch": patch,
            "budget": runtime.snapshot(),
            "addressed_signal_ids": decision.addressed_signal_ids,
        }

    async def admit_node(state: GraphRunState) -> dict[str, Any]:
        patch = state["pending_patch"]
        with runtime.observe(
            kind="graph_patch",
            actor="admission",
            name=f"patch:{patch.patch_id}",
            input={
                "rationale": patch.rationale[:300],
                "add_nodes": [(n.node_id, n.kind) for n in patch.add_nodes],
                "cancel": patch.cancel_node_ids,
                "stop": patch.stop,
            },
        ) as rec:
            new_graph, decision = admit_patch(state["run_graph"], patch, caps=caps)
            rec.set_output(
                {
                    "admitted": decision.admitted,
                    "accepted": decision.accepted_node_ids,
                    "rejected_nodes": decision.rejected_node_ids,
                    "prev_hash": decision.prev_graph_hash,
                    "new_hash": decision.new_graph_hash,
                }
            )
            verdict = "admitted" if decision.admitted else "rejected"
            rec.note(
                summary=(
                    f"patch {verdict}: +{decision.accepted_node_ids} "
                    f"{decision.prev_graph_hash} -> {decision.new_graph_hash}"
                ),
                **decision.model_dump(),
            )

        stopping = state.get("stopping", False) or (patch.stop and decision.admitted)
        stall = 0 if (decision.accepted_node_ids or patch.stop) else state.get("stall", 0) + 1
        updates: dict[str, Any] = {
            "run_graph": new_graph,
            "patch_decisions": [decision],
            "stopping": stopping,
            "stall": stall,
            "pending_patch": None,
        }
        if decision.admitted:
            updates["applied_patches"] = [patch]
        return updates

    async def execute_node(state: dict[str, Any]) -> dict[str, Any]:
        node: GraphNode = state["node"]
        node_id: str = state["node_id"]
        visible: list[Artifact] = state["visible_artifacts"]
        span_kind = "validation" if node.kind == "validate" else node.kind

        with runtime.observe(
            kind=span_kind,
            actor=node_id,
            name=f"node:{node.kind}:{node_id}",
            input={"objective": node.objective, "inputs": node.input_artifact_ids},
            metadata={"node_kind": node.kind},
        ) as rec:

            async def _dispatch() -> WorkerResult:
                if node.kind == "research":
                    return await research_agent.run(node, deps)
                if node.kind == "quant":
                    return await quant_agent.run(node, deps, visible)
                if node.kind == "critic":
                    return await critic_agent.run(node, deps, visible)
                if node.kind == "forecast":
                    return await forecaster_agent.run(node, deps, visible, state["cruxes"])
                if node.kind == "validate":
                    return _run_validate(
                        node, deps, visible, state["estimates"], state["cruxes"], rec
                    )
                return WorkerResult(error=f"unknown node kind {node.kind}")

            try:
                result = await asyncio.wait_for(_dispatch(), node.max_runtime_seconds)
            except TimeoutError:
                result = WorkerResult(error=f"node timed out after {node.max_runtime_seconds}s")
            except CapExceeded as exc:
                result = WorkerResult(error=f"cap reached: {exc}")
            except Exception as exc:  # noqa: BLE001 - record failure, keep the run alive
                result = WorkerResult(error=f"{type(exc).__name__}: {exc}")

            emitted = [a.id for a in result.artifacts]
            status = "failed" if result.error else "completed"
            rec.set_output(
                {
                    "status": status,
                    "emitted": emitted,
                    "signals": len(result.signals),
                    "cruxes": len(result.cruxes),
                    "estimates": len(result.estimates),
                }
            )
            rec.note(
                summary=f"{node.kind}:{node_id} -> {len(emitted)} artifacts"
                + (f" [ERROR: {result.error}]" if result.error else ""),
                status=status,
                emitted=emitted,
                error=result.error,
            )

        return {
            "artifacts": result.artifacts,
            "signals": result.signals,
            "cruxes": result.cruxes,
            "estimates": result.estimates,
            "final": result.final,
            "completed": [node_id],
            "node_errors": [{"node_id": node_id, "error": result.error}] if result.error else [],
        }

    async def reduce_node(state: GraphRunState) -> dict[str, Any]:
        with runtime.observe(kind="reduce", actor="runtime", name="reduce") as rec:
            new_graph = state["run_graph"].model_copy(deep=True)
            errored = {e["node_id"] for e in state.get("node_errors", [])}
            artifacts = state.get("artifacts", [])
            completed: list[str] = []
            failed: list[str] = []
            for node_id in set(state.get("completed", [])):
                node = new_graph.nodes.get(node_id)
                if node and node.status == "pending":
                    if node_id in errored:
                        node.status = "failed"
                        failed.append(node_id)
                    else:
                        node.status = "completed"
                        completed.append(node_id)
                    node.emitted_artifact_ids = [a.id for a in artifacts if a.created_by == node_id]
            new_hash = new_graph.hash()
            rec.set_output({"completed": completed, "failed": failed, "graph_hash": new_hash})
            rec.note(
                summary=f"reduced +{completed} failed={failed} -> {new_hash}",
                completed=completed,
                failed=failed,
                graph_hash=new_hash,
                budget=runtime.snapshot().model_dump(),
            )
        return {"run_graph": new_graph, "budget": runtime.snapshot()}

    async def finalise_node(state: GraphRunState) -> dict[str, Any]:
        final = state.get("final")
        if final is None:
            with runtime.observe(kind="validation", actor="finaliser", name="finalise") as rec:
                final, report = finalise_forecast(
                    question=deps.question,
                    estimates=state.get("estimates", []),
                    artifacts=state.get("artifacts", []),
                    cruxes=state.get("cruxes", []),
                )
                rec.note(
                    summary=f"validator {report.verdict} p={final.probability:.2f}",
                    verdict=report.verdict,
                    checks=[c.model_dump() for c in report.checks],
                    probability=final.probability,
                )
        runtime.emit(
            "final_forecast",
            "finaliser",
            f"final p={final.probability:.2f} via {final.method}",
            probability=final.probability,
            method=final.method,
            component_estimate_ids=final.component_estimate_ids,
            source_ids=final.source_ids,
            model_card_ids=final.model_card_ids,
            calibration_note=final.calibration_note,
        )
        _persist(runtime, state, final)
        return {"final": final}

    def route_dispatch(state: GraphRunState):
        ready = ready_wave(state["run_graph"], caps)
        if ready:
            return [Send("execute", send_payload(state, node_id)) for node_id in ready]
        budget = state["budget"]
        if (
            state.get("stopping")
            or budget.iterations >= caps.max_iterations
            or state.get("stall", 0) >= 2
        ):
            return "finalise"
        return "plan"

    builder = StateGraph(GraphRunState)
    builder.add_node("plan", plan_node)
    builder.add_node("admit", admit_node)
    builder.add_node("execute", execute_node)
    builder.add_node("reduce", reduce_node)
    builder.add_node("finalise", finalise_node)

    builder.add_edge(START, "plan")
    builder.add_edge("plan", "admit")
    builder.add_conditional_edges("admit", route_dispatch, ["execute", "plan", "finalise"])
    builder.add_edge("execute", "reduce")
    builder.add_conditional_edges("reduce", route_dispatch, ["execute", "plan", "finalise"])
    builder.add_edge("finalise", END)
    return builder.compile()


async def run_forecast(
    deps: AgentDeps, *, recursion_limit: int | None = None
) -> FinalForecast | None:
    app = build_app(deps)
    seed = GraphNode(node_id="seed", kind="seed", objective=deps.question.title, status="completed")
    state0 = initial_state(seed)
    caps = deps.runtime.caps
    # Scale the recursion limit to the real drivers (planner turns and the
    # execute/reduce waves needed to drain the nodes), not a flat constant.
    waves = math.ceil(caps.max_graph_nodes / max(1, caps.max_wave_workers))
    limit = recursion_limit or (caps.max_iterations * (2 + 2 * waves) + 30)

    with deps.runtime.run_trace() as run_rec:
        final_state = await app.ainvoke(state0, config={"recursion_limit": limit})
        final: FinalForecast | None = final_state.get("final")
        run_rec.set_output(
            {
                "probability": final.probability if final else None,
                "method": final.method if final else None,
            }
        )
        deps.runtime.tracer.set_trace_io(
            output={"probability": final.probability if final else None}
        )
    return final
