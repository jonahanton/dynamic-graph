from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from dynamic_graph.domain.artifacts import Artifact


class DiagnosticCheck(BaseModel):
    name: str
    status: Literal["pass", "warn", "fail"]
    detail: str = ""
    observed: float | None = None
    threshold: float | None = None


class QuantEstimate(BaseModel):
    """A flexible named numeric result. The analysis can emit zero or more of
    these (a base rate, a threshold-crossing probability, a sensitivity bound,
    a fitted parameter) without being forced into a fixed shape."""

    name: str
    value: float
    interval_low: float | None = None
    interval_high: float | None = None
    note: str = ""


class ModelCard(BaseModel):
    """The single, flexible typed quant output. `method` and `findings` are free
    prose so the agent is not pigeon-holed; the structured fields carry the
    estimates, diagnostics and lineage the forecaster and validator rely on. A
    card with `n_draws > 0` is registered as a `simulation` artefact."""

    model_card_id: str
    created_by: str
    target_estimand: str
    method: str  # free-text, e.g. "beta-binomial base rate" or "Monte Carlo over scenarios"
    findings: str = ""
    confidence: Literal["low", "moderate", "high"] = "moderate"

    headline_probability: float | None = None
    interval_low: float | None = None
    interval_high: float | None = None
    estimates: list[QuantEstimate] = Field(default_factory=list)

    assumptions: list[str] = Field(default_factory=list)
    priors: dict[str, Any] = Field(default_factory=dict)
    diagnostics: list[DiagnosticCheck] = Field(default_factory=list)
    sensitivity: list[DiagnosticCheck] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    forecast_contribution: str = ""

    # simulation metadata (optional; presence promotes the artefact kind)
    n_draws: int = 0
    scenarios: list[dict[str, Any]] = Field(default_factory=list)

    # lineage
    source_ids: list[str] = Field(default_factory=list)
    input_dataset_ids: list[str] = Field(default_factory=list)
    data_cutoff: str | None = None
    as_of: str | None = None
    code_paths: list[str] = Field(default_factory=list)
    code_hashes: list[str] = Field(default_factory=list)
    output_paths: list[str] = Field(default_factory=list)
    package_versions: dict[str, str] = Field(default_factory=dict)
    execution_summary: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    observation_id: str | None = None

    @property
    def is_simulation(self) -> bool:
        return self.n_draws > 0


class QuantGap(BaseModel):
    gap_id: str
    created_by: str
    missing: str
    needed_for: str
    suggested_search: str = ""


def to_artifact(output: ModelCard | QuantGap) -> Artifact:
    """Register a typed quant output as a blackboard artefact with lineage."""
    if isinstance(output, ModelCard):
        kind = "simulation" if output.is_simulation else "model_card"
        label = f"{output.method}: {output.target_estimand}"
        return Artifact(
            id=output.model_card_id,
            kind=kind,
            created_by=output.created_by,
            work_id=output.created_by,
            summary=label[:160],
            payload=output.model_dump(),
            source_ids=output.source_ids,
            file_paths=output.code_paths + output.output_paths,
            trace_id=output.trace_id,
            observation_id=output.observation_id,
        )
    return Artifact(
        id=output.gap_id,
        kind="quant_gap",
        created_by=output.created_by,
        work_id=output.created_by,
        summary=f"quant gap: {output.missing}"[:160],
        payload=output.model_dump(),
    )
