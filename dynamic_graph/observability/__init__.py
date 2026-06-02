from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from dynamic_graph.config.settings import Settings
from dynamic_graph.domain.budget import Caps
from dynamic_graph.domain.questions import ForecastQuestion
from dynamic_graph.observability.events import Event, EventLog
from dynamic_graph.observability.langfuse import (
    InMemoryTracer,
    LangfuseTracer,
    Tracer,
    build_langfuse_tracer,
)
from dynamic_graph.observability.runtime import CapExceeded, ObservedRuntime, Recorder, RunPaths


def new_run_id(question: ForecastQuestion) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", question.title.lower()).strip("-")[:32].strip("-") or "run"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{slug}-{stamp}-{uuid.uuid4().hex[:6]}"


def build_runtime(
    *,
    question: ForecastQuestion,
    caps: Caps,
    settings: Settings | None = None,
    tracer: Tracer | None = None,
    runs_root: Path = Path("runs"),
    run_id: str | None = None,
) -> ObservedRuntime:
    """Build a runtime for a run. Real runs pass `settings` and a Langfuse tracer
    is built (failing fast on missing credentials); tests inject a tracer."""
    rid = run_id or new_run_id(question)
    paths = RunPaths.for_run(runs_root, rid)
    if tracer is None:
        if settings is None:
            raise ValueError("build_runtime needs either a tracer or settings")
        tracer = build_langfuse_tracer(settings)
    events = EventLog(rid, paths.events)
    return ObservedRuntime(
        run_id=rid, question=question, tracer=tracer, events=events, caps=caps, paths=paths
    )


__all__ = [
    "Event",
    "EventLog",
    "InMemoryTracer",
    "LangfuseTracer",
    "Tracer",
    "build_langfuse_tracer",
    "CapExceeded",
    "ObservedRuntime",
    "Recorder",
    "RunPaths",
    "new_run_id",
    "build_runtime",
]
