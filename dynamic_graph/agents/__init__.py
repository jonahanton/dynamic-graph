from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from dynamic_graph.connectors import ObservedWeb
from dynamic_graph.domain.artifacts import Artifact, OpenCrux, ResearchSignal
from dynamic_graph.domain.forecasts import FinalForecast, ForecastEstimate
from dynamic_graph.domain.graph import GraphNode
from dynamic_graph.domain.questions import ForecastQuestion
from dynamic_graph.llm import ObservedLLM
from dynamic_graph.observability.runtime import ObservedRuntime
from dynamic_graph.quant import ObservedQuant

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@cache
def load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def question_header(question: ForecastQuestion) -> str:
    return (
        f"Question: {question.title}\n"
        f"Resolution: {question.resolution_criteria}\n"
        f"as_of: {question.as_of.isoformat()}\n"
    )


def node_brief(node: GraphNode) -> str:
    """Render the master's dynamically written briefing for a worker node."""
    text = f"Your node: {node.node_id} ({node.kind})\nObjective: {node.objective}\n"
    if node.brief:
        text += f"\nMaster's brief for this node:\n{node.brief}\n"
    return text


@dataclass
class AgentDeps:
    """The observed capabilities every agent receives. Agents never see raw
    provider/search/quant clients."""

    runtime: ObservedRuntime
    llm: ObservedLLM
    web: ObservedWeb
    quant: ObservedQuant
    question: ForecastQuestion


@dataclass
class WorkerResult:
    artifacts: list[Artifact] = field(default_factory=list)
    signals: list[ResearchSignal] = field(default_factory=list)
    cruxes: list[OpenCrux] = field(default_factory=list)
    estimates: list[ForecastEstimate] = field(default_factory=list)
    final: FinalForecast | None = None
    error: str | None = None


__all__ = [
    "AgentDeps",
    "WorkerResult",
    "load_prompt",
    "new_id",
    "question_header",
    "node_brief",
]
