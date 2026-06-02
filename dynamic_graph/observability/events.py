from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Event(BaseModel):
    """One observed action, mirrored to local JSONL alongside its Langfuse ids."""

    run_id: str
    seq: int
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: str
    actor: str
    summary: str = ""
    trace_id: str | None = None
    observation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventLog:
    """Append-only JSONL writer for a single run. Synchronous appends are safe
    under asyncio (no await between sequence increment and write)."""

    def __init__(self, run_id: str, path: Path) -> None:
        self.run_id = run_id
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._handle = self.path.open("a", encoding="utf-8")

    def append(
        self,
        *,
        kind: str,
        actor: str,
        summary: str = "",
        payload: dict[str, Any] | None = None,
        trace_id: str | None = None,
        observation_id: str | None = None,
    ) -> Event:
        self._seq += 1
        event = Event(
            run_id=self.run_id,
            seq=self._seq,
            kind=kind,
            actor=actor,
            summary=summary,
            trace_id=trace_id,
            observation_id=observation_id,
            payload=payload or {},
        )
        self._handle.write(event.model_dump_json() + "\n")
        self._handle.flush()
        return event

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    @staticmethod
    def read(path: Path) -> list[Event]:
        events: list[Event] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(Event.model_validate_json(line))
        return events
