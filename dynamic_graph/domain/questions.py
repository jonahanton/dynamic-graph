from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ForecastQuestion(BaseModel):
    """A binary forecasting question with point-in-time framing."""

    question_id: str
    title: str
    resolution_criteria: str
    body: str = ""
    resolution_source: str = ""
    as_of: datetime
    close_time: datetime | None = None
    created_at: datetime
