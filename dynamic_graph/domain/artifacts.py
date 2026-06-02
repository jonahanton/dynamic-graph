from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ArtifactKind = Literal[
    "source",
    "source_coverage",
    "dataset",
    "data_quality",
    "model_card",
    "simulation",
    "quant_gap",
    "evidence",
    "crux",
    "estimate",
    "final",
]


def _now() -> datetime:
    return datetime.now(UTC)


class Artifact(BaseModel):
    """The shared blackboard object every worker can publish."""

    id: str
    kind: ArtifactKind
    created_by: str
    summary: str = ""
    work_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    source_ids: list[str] = Field(default_factory=list)
    file_paths: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    observation_id: str | None = None
    created_at: datetime = Field(default_factory=_now)


class ResearchSignal(BaseModel):
    """A worker hint that more work might be valuable. Workers cannot patch the
    graph; they raise signals and the master decides whether to act."""

    id: str
    created_by: str
    kind: Literal[
        "missing_data",
        "follow_up_search",
        "robustness_check",
        "resolution_ambiguity",
        "open_crux",
    ]
    description: str
    rationale: str = ""
    suggested_node_kind: Literal["research", "quant", "critic"] | None = None
    addressed: bool = False
    created_at: datetime = Field(default_factory=_now)


class OpenCrux(BaseModel):
    """An unresolved question that could move the forecast."""

    id: str
    created_by: str
    description: str
    status: Literal["open", "resolved", "dismissed"] = "open"
    critical: bool = False
    source_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
