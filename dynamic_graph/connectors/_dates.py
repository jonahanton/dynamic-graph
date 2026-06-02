from __future__ import annotations

from datetime import UTC, datetime, timedelta

_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%a, %d %b %Y %H:%M:%S %z",
)


def parse_date(value: object) -> datetime | None:
    """Parse a date defensively, always returning timezone-aware UTC or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return _to_utc(dt)
    except ValueError:
        pass
    for fmt in _FORMATS:
        try:
            return _to_utc(datetime.strptime(raw, fmt))
        except ValueError:
            continue
    return None


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def freshness_range(upper: object, *, lookback_days: int = 3650) -> str | None:
    """Brave freshness window `YYYY-MM-DDtoYYYY-MM-DD` ending at `upper` (the as_of
    date), with a wide lower bound so older sources are still found."""
    end = parse_date(upper)
    if end is None:
        return None
    start = end - timedelta(days=lookback_days)
    return f"{start.date().isoformat()}to{end.date().isoformat()}"


def to_iso8601(value: object) -> str | None:
    """ISO 8601 UTC instant (e.g. `2026-06-01T00:00:00.000Z`) as Exa requires."""
    dt = parse_date(value)
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
