from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from dynamic_graph.quant.outputs import DiagnosticCheck

# All schemas here are LLM-facing: primitives, enums and nested objects only
# (no free-form dicts or datetimes), so they pass strict structured-output APIs.


# -- master / planner ------------------------------------------------------- #


class PatchNodeSpec(BaseModel):
    node_id: str
    kind: Literal["research", "quant", "critic", "forecast", "validate"]
    objective: str  # a short label for the node
    brief: str  # a thorough, node-specific briefing: context + instructions
    depends_on: list[str] = Field(default_factory=list)
    input_artifact_ids: list[str] = Field(default_factory=list)


class PlannerDecision(BaseModel):
    rationale: str
    add_nodes: list[PatchNodeSpec] = Field(default_factory=list)
    cancel_node_ids: list[str] = Field(default_factory=list)
    stop: bool = False
    addressed_signal_ids: list[str] = Field(default_factory=list)
    ignored_signals_note: str = ""


# -- research --------------------------------------------------------------- #


class SignalSpec(BaseModel):
    kind: Literal[
        "missing_data", "follow_up_search", "robustness_check", "resolution_ambiguity", "open_crux"
    ]
    description: str
    suggested_node_kind: Literal["research", "quant", "critic", "none"] = "none"


class SearchQuery(BaseModel):
    query: str
    provider: Literal["brave", "exa", "any"] = "any"
    freshness: Literal["none", "pd", "pw", "pm", "py"] = "none"


class SearchPlan(BaseModel):
    rationale: str
    queries: list[SearchQuery] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    claim: str
    stance: Literal["supports_yes", "supports_no", "neutral"]
    strength: Literal["weak", "moderate", "strong"]
    source_url: str
    quote: str


class ResearchOutput(BaseModel):
    summary: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    signals: list[SignalSpec] = Field(default_factory=list)


# -- quant ------------------------------------------------------------------ #


class QuantPlan(BaseModel):
    decision: Literal["model", "gap"]
    approach: str
    analysis_code: str = ""
    # populated when decision == "gap"
    gap_missing: str = ""
    gap_needed_for: str = ""
    gap_suggested_search: str = ""


class QuantNumber(BaseModel):
    """A flexible named numeric result the analysis produced."""

    name: str
    value: float
    interval_low: float | None = None
    interval_high: float | None = None
    note: str = ""


class QuantReport(BaseModel):
    """Flexible, prose-friendly summary of a quant node. The agent describes its
    method and findings freely; only `is_gap` and the lineage (filled by the
    runtime) are load-bearing. `headline_probability` is the single
    resolution-relevant number when the analysis yields one."""

    is_gap: bool = False
    target_estimand: str = ""
    method: str = ""  # free prose: what was actually done
    findings: str = ""  # free prose: what the results mean
    confidence: Literal["low", "moderate", "high"] = "moderate"

    headline_probability: float | None = None
    interval_low: float | None = None
    interval_high: float | None = None
    estimates: list[QuantNumber] = Field(default_factory=list)

    assumptions: list[str] = Field(default_factory=list)
    priors: list[str] = Field(default_factory=list)
    diagnostics: list[DiagnosticCheck] = Field(default_factory=list)
    sensitivity: list[DiagnosticCheck] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    forecast_contribution: str = ""

    n_draws: int = 0

    gap_missing: str = ""
    gap_needed_for: str = ""


# -- critic ----------------------------------------------------------------- #


class CruxSpec(BaseModel):
    description: str
    critical: bool = False


class Critique(BaseModel):
    summary: str
    cruxes: list[CruxSpec] = Field(default_factory=list)
    signals: list[SignalSpec] = Field(default_factory=list)


# -- forecaster ------------------------------------------------------------- #


class ForecastOutput(BaseModel):
    probability: float
    rationale: str
    key_drivers: list[str] = Field(default_factory=list)
    # ids of the model cards actually relied on (from those shown as trustworthy)
    used_model_card_ids: list[str] = Field(default_factory=list)
