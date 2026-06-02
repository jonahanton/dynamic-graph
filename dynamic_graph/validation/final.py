from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from dynamic_graph.domain.artifacts import Artifact, OpenCrux
from dynamic_graph.domain.forecasts import FinalForecast, ForecastEstimate
from dynamic_graph.domain.questions import ForecastQuestion

CALIBRATION_NOTE = "identity-baseline: raw aggregate, no live calibration applied"


class ValidationCheck(BaseModel):
    name: str
    status: Literal["pass", "warn", "fail"]
    detail: str = ""


class ValidationReport(BaseModel):
    verdict: Literal["accepted", "accepted_with_warnings", "rejected"]
    checks: list[ValidationCheck] = Field(default_factory=list)

    @property
    def warnings(self) -> list[str]:
        return [c.detail for c in self.checks if c.status == "warn"]

    @property
    def failures(self) -> list[str]:
        return [c.detail for c in self.checks if c.status == "fail"]


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def validate(
    *,
    question: ForecastQuestion,
    estimates: list[ForecastEstimate],
    artifacts: list[Artifact],
    cruxes: list[OpenCrux],
) -> ValidationReport:
    checks: list[ValidationCheck] = []

    sources = [a for a in artifacts if a.kind in ("source", "evidence")]
    checks.append(
        ValidationCheck(
            name="source_support",
            status="pass" if sources else "fail",
            detail=f"{len(sources)} source/evidence artefacts support the forecast",
        )
    )

    model_cards = [a for a in artifacts if a.kind in ("model_card", "simulation")]
    used_card_ids = {cid for e in estimates for cid in e.model_card_ids}
    used_cards = [c for c in model_cards if c.id in used_card_ids]
    if not used_cards:
        checks.append(
            ValidationCheck(
                name="quant_diagnostics",
                status="pass",
                detail="no quant model cards used by the forecast",
            )
        )
    for card in used_cards:
        diagnostics = card.payload.get("diagnostics", [])
        failing = [d for d in diagnostics if d.get("status") == "fail"]
        has_lineage = bool(card.payload.get("code_hashes")) and bool(
            card.payload.get("package_versions")
        )
        if not diagnostics:
            status, detail = "fail", f"{card.id} used without diagnostics"
        elif not has_lineage:
            status, detail = "fail", f"{card.id} used without code/package lineage"
        elif failing:
            status, detail = "warn", f"{card.id} has failing diagnostics"
        else:
            status, detail = "pass", f"{card.id} diagnostics and lineage ok"
        checks.append(ValidationCheck(name="quant_diagnostics", status=status, detail=detail))

    leaked = [
        a.id
        for a in sources
        if (dt := _parse_dt(a.payload.get("published_at"))) is not None and dt > question.as_of
    ]
    undated = sum(1 for a in sources if _parse_dt(a.payload.get("published_at")) is None)
    checks.append(
        ValidationCheck(
            name="point_in_time",
            status="fail" if leaked else "pass",
            detail=(
                f"sources published after as_of: {leaked}"
                if leaked
                else f"no source dated after as_of ({undated} undated)"
            ),
        )
    )

    critical_open = [c for c in cruxes if c.critical and c.status == "open"]
    checks.append(
        ValidationCheck(
            name="critical_cruxes",
            status="warn" if critical_open else "pass",
            detail=(
                f"{len(critical_open)} unresolved critical cruxes"
                if critical_open
                else "no blocking cruxes"
            ),
        )
    )

    bounded = all(0.0 <= e.probability <= 1.0 for e in estimates)
    checks.append(
        ValidationCheck(
            name="probability_bounds",
            status="pass" if bounded else "fail",
            detail="all estimates within [0, 1]" if bounded else "an estimate is out of bounds",
        )
    )

    if any(c.status == "fail" for c in checks):
        verdict: Literal["accepted", "accepted_with_warnings", "rejected"] = "rejected"
    elif any(c.status == "warn" for c in checks):
        verdict = "accepted_with_warnings"
    else:
        verdict = "accepted"
    return ValidationReport(verdict=verdict, checks=checks)


def aggregate_probabilities(probabilities: list[float]) -> tuple[float, str]:
    """Combine independent probability estimates.

    A single estimate is passed through unchanged. Multiple estimates are pooled
    in log-odds space (the geometric mean of the odds), which is better
    calibrated for probabilities than a plain arithmetic mean and avoids the
    regression-to-0.5 that linear averaging causes.
    """
    if not probabilities:
        return 0.5, "no_estimates_fallback"
    if len(probabilities) == 1:
        return probabilities[0], "single_estimate"
    clipped = [min(max(p, 1e-6), 1 - 1e-6) for p in probabilities]
    mean_logit = sum(math.log(p / (1 - p)) for p in clipped) / len(clipped)
    pooled = 1 / (1 + math.exp(-mean_logit))
    return pooled, "logit_pool"


def _aggregate(estimates: list[ForecastEstimate]) -> tuple[float, str, str]:
    blind = [e for e in estimates if e.blind] or estimates
    if not blind:
        return 0.5, "no_estimates_fallback", "No forecast estimate was produced; defaulting to 0.5."
    probability, method = aggregate_probabilities([e.probability for e in blind])
    rationale = " | ".join(e.rationale for e in blind)[:1200]
    if len(blind) > 1:
        lo = min(e.probability for e in blind)
        hi = max(e.probability for e in blind)
        rationale = f"[{method} of {len(blind)} estimates, spread {lo:.2f}-{hi:.2f}] {rationale}"
    return probability, method, rationale


def finalise_forecast(
    *,
    question: ForecastQuestion,
    estimates: list[ForecastEstimate],
    artifacts: list[Artifact],
    cruxes: list[OpenCrux],
) -> tuple[FinalForecast, ValidationReport]:
    report = validate(question=question, estimates=estimates, artifacts=artifacts, cruxes=cruxes)
    probability, method, rationale = _aggregate(estimates)

    note = CALIBRATION_NOTE
    if report.warnings:
        note += " | warnings: " + "; ".join(report.warnings)

    final = FinalForecast(
        id=f"final-{uuid.uuid4().hex[:8]}",
        probability=round(probability, 4),
        rationale=rationale,
        method=method,
        component_estimate_ids=[e.id for e in estimates],
        source_ids=sorted({sid for e in estimates for sid in e.source_ids}),
        model_card_ids=sorted({cid for e in estimates for cid in e.model_card_ids}),
        calibration_note=note,
    )
    return final, report
