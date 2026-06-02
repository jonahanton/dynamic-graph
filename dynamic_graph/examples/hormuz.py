from __future__ import annotations

import uuid
from datetime import UTC, datetime

from dynamic_graph.domain.questions import ForecastQuestion


def hormuz_question() -> ForecastQuestion:
    as_of = datetime(2026, 6, 1, tzinfo=UTC)
    return ForecastQuestion(
        question_id="strait-of-hormuz",
        title=(
            "Will the Strait of Hormuz sustain at least 100 ship transits per day on a "
            "7-day moving average by 2026-06-30?"
        ),
        resolution_criteria=(
            "Resolve YES if, on or before 2026-06-30, a reputable shipping or official "
            "maritime source reports Strait of Hormuz traffic sustaining at least 100 ship "
            "transits per day on a seven-day moving-average basis. Otherwise resolve NO."
        ),
        resolution_source="Reputable shipping tracker or official maritime authority.",
        as_of=as_of,
        close_time=datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC),
        created_at=as_of,
    )


def question_from_text(text: str, *, as_of: datetime | None = None) -> ForecastQuestion:
    now = as_of or datetime.now(UTC)
    return ForecastQuestion(
        question_id=f"adhoc-{uuid.uuid4().hex[:8]}",
        title=text.strip(),
        resolution_criteria=(
            "Resolve YES if the event described in the question occurs by the stated date; "
            "otherwise resolve NO."
        ),
        as_of=now,
        created_at=now,
    )
