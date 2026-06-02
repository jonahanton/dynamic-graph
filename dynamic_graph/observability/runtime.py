from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dynamic_graph.domain.budget import BudgetState, Caps
from dynamic_graph.domain.questions import ForecastQuestion
from dynamic_graph.observability.events import EventLog
from dynamic_graph.observability.langfuse import SpanHandle, Tracer

# Langfuse renders a trace as an agent graph from observations whose type is not
# span/event/generation, so map our action kinds to agent-graph types.
_OBSERVATION_TYPES = {
    "run": "agent",
    "planner_signals": "agent",
    "planner_fallback": "agent",
    "research": "retriever",
    "quant": "tool",
    "critic": "agent",
    "forecast": "agent",
    "validation": "evaluator",
    "graph_patch": "chain",
    "reduce": "chain",
    "web_search": "tool",
    "fetch": "tool",
    "quant_exec": "tool",
    "final_forecast": "chain",
}


class CapExceeded(RuntimeError):
    """Raised when a hard cap would be exceeded by an external action."""


@dataclass
class RunPaths:
    root: Path
    events: Path
    state: Path
    workspace: Path

    @classmethod
    def for_run(cls, runs_root: Path, run_id: str) -> RunPaths:
        root = runs_root / run_id
        workspace = root / "workspace"
        for sub in ("raw", "derived", "models", "quant"):
            (workspace / sub).mkdir(parents=True, exist_ok=True)
        return cls(
            root=root, events=root / "events.jsonl", state=root / "state.json", workspace=workspace
        )


class Recorder:
    """Yielded inside `observe`. Enriches both the Langfuse span and JSONL event."""

    def __init__(self, span: SpanHandle) -> None:
        self._span = span
        self.summary = ""
        self.payload: dict[str, Any] = {}

    @property
    def trace_id(self) -> str | None:
        return self._span.trace_id

    @property
    def observation_id(self) -> str | None:
        return self._span.id

    def set_output(
        self,
        output: Any,
        *,
        usage: dict[str, int] | None = None,
        cost: dict[str, float] | None = None,
        model: str | None = None,
    ) -> None:
        self._span.update(output=output, usage=usage, cost=cost, model=model)

    def annotate(self, **metadata: Any) -> None:
        self._span.update(metadata=metadata)

    def note(self, summary: str | None = None, **payload: Any) -> None:
        """Record fields for the local JSONL mirror (and human-readable tail)."""
        if summary is not None:
            self.summary = summary
        self.payload.update(payload)


class ObservedRuntime:
    """The single place that talks to Langfuse and the local event stream, and
    the single gatekeeper that enforces caps before any external action."""

    def __init__(
        self,
        *,
        run_id: str,
        question: ForecastQuestion,
        tracer: Tracer,
        events: EventLog,
        caps: Caps,
        paths: RunPaths,
    ) -> None:
        self.run_id = run_id
        self.question = question
        self.tracer = tracer
        self.events = events
        self.caps = caps
        self.paths = paths
        self.budget = BudgetState()

    # -- observation -------------------------------------------------------- #

    @contextlib.contextmanager
    def observe(
        self,
        *,
        kind: str,
        actor: str,
        name: str | None = None,
        as_generation: bool = False,
        model: str | None = None,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
        model_parameters: dict[str, Any] | None = None,
    ) -> Iterator[Recorder]:
        span_name = name or f"{kind}:{actor}"
        as_type = "generation" if as_generation else _OBSERVATION_TYPES.get(kind, "span")
        with self.tracer.observation(
            span_name,
            as_type=as_type,
            model=model,
            input=input,
            metadata=metadata,
            model_parameters=model_parameters,
        ) as span:
            recorder = Recorder(span)
            try:
                yield recorder
            except Exception as exc:  # noqa: BLE001 - record then re-raise
                recorder.note(error=f"{type(exc).__name__}: {exc}")
                span.update(metadata={"error": str(exc)})
                raise
            finally:
                self.events.append(
                    kind=kind,
                    actor=actor,
                    summary=recorder.summary,
                    payload=recorder.payload,
                    trace_id=recorder.trace_id,
                    observation_id=recorder.observation_id,
                )

    @contextlib.contextmanager
    def run_trace(self) -> Iterator[Recorder]:
        with self.observe(
            kind="run",
            actor="runtime",
            name=f"forecast-run:{self.run_id}",
            input={
                "question": self.question.title,
                "resolution_criteria": self.question.resolution_criteria,
                "as_of": self.question.as_of.isoformat(),
            },
        ) as recorder:
            self.tracer.set_trace_io(input={"question": self.question.title})
            yield recorder

    def emit(self, kind: str, actor: str, summary: str, **payload: Any) -> None:
        """A point-in-time event: a short Langfuse observation mirrored to JSONL."""
        as_type = _OBSERVATION_TYPES.get(kind, "span")
        with self.tracer.observation(f"{kind}:{actor}", as_type=as_type, input=payload) as span:
            span.update(output={"summary": summary})
            self.events.append(
                kind=kind,
                actor=actor,
                summary=summary,
                payload=payload,
                trace_id=span.trace_id,
                observation_id=span.id,
            )

    # -- guards & caps ------------------------------------------------------ #

    def require_active_observation(self) -> None:
        if self.tracer.current_trace_id() is None:
            raise RuntimeError("Refusing external action with no active Langfuse observation.")

    def charge_llm(self) -> None:
        self.require_active_observation()
        if self.budget.llm_calls >= self.caps.max_llm_calls:
            raise CapExceeded(f"max_llm_calls ({self.caps.max_llm_calls}) reached")
        self.budget.llm_calls += 1

    def charge_search(self) -> None:
        self.require_active_observation()
        if self.budget.search_calls >= self.caps.max_search_calls:
            raise CapExceeded(f"max_search_calls ({self.caps.max_search_calls}) reached")
        self.budget.search_calls += 1

    def charge_fetch(self) -> None:
        self.require_active_observation()
        if self.budget.fetch_calls >= self.caps.max_fetch_calls:
            raise CapExceeded(f"max_fetch_calls ({self.caps.max_fetch_calls}) reached")
        self.budget.fetch_calls += 1

    def charge_quant(self) -> None:
        self.require_active_observation()
        if self.budget.quant_executions >= self.caps.max_quant_executions:
            raise CapExceeded(f"max_quant_executions ({self.caps.max_quant_executions}) reached")
        self.budget.quant_executions += 1

    def add_cost(self, micros: int) -> None:
        self.budget.cost_micros += micros

    def bump_iteration(self) -> int:
        self.budget.iterations += 1
        return self.budget.iterations

    def snapshot(self) -> BudgetState:
        return self.budget.model_copy()

    def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            self.tracer.flush()
        with contextlib.suppress(Exception):
            self.tracer.shutdown()
        self.events.close()
