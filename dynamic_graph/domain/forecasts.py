from __future__ import annotations

import math
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator


def _now() -> datetime:
    return datetime.now(UTC)


def _bounded(value: float) -> float:
    if math.isnan(value) or not 0.0 <= value <= 1.0:
        raise ValueError("probability must be a finite value in [0, 1]")
    return value


class ForecastEstimate(BaseModel):
    """A single probability estimate, blind by default."""

    id: str
    created_by: str
    probability: float
    rationale: str
    method: str = "blind"
    blind: bool = True
    source_ids: list[str] = Field(default_factory=list)
    model_card_ids: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    created_at: datetime = Field(default_factory=_now)

    _check_probability = field_validator("probability")(staticmethod(_bounded))


class FinalForecast(BaseModel):
    """The accepted forecast for the run."""

    id: str
    probability: float
    rationale: str
    method: str = "equal_weighted_mean"
    component_estimate_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    model_card_ids: list[str] = Field(default_factory=list)
    calibration_note: str = ""
    created_at: datetime = Field(default_factory=_now)

    _check_probability = field_validator("probability")(staticmethod(_bounded))
