from __future__ import annotations

import contextlib
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from dynamic_graph.config.settings import Settings


class SpanHandle(ABC):
    """A handle to one open observation. Exposes the ids we mirror into JSONL."""

    id: str | None
    trace_id: str | None

    @abstractmethod
    def update(
        self,
        *,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
        cost: dict[str, float] | None = None,
        model: str | None = None,
    ) -> None: ...


class Tracer(ABC):
    """The only observability seam the rest of the code depends on. The real
    implementation talks to Langfuse; tests use the in-memory implementation."""

    @abstractmethod
    def observation(
        self,
        name: str,
        *,
        as_type: str = "span",
        model: str | None = None,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
        model_parameters: dict[str, Any] | None = None,
    ) -> contextlib.AbstractContextManager[SpanHandle]: ...

    @abstractmethod
    def set_trace_io(self, *, input: Any | None = None, output: Any | None = None) -> None: ...

    @abstractmethod
    def current_trace_id(self) -> str | None: ...

    @abstractmethod
    def flush(self) -> None: ...

    @abstractmethod
    def shutdown(self) -> None: ...


# --------------------------------------------------------------------------- #
# Real Langfuse adapter (targets the v4 unified observation API).
# --------------------------------------------------------------------------- #


class _LangfuseSpan(SpanHandle):
    def __init__(self, obj: Any) -> None:
        self._obj = obj
        self.id = getattr(obj, "id", None)
        self.trace_id = getattr(obj, "trace_id", None)

    def update(
        self,
        *,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
        cost: dict[str, float] | None = None,
        model: str | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if output is not None:
            kwargs["output"] = output
        if metadata:
            kwargs["metadata"] = metadata
        if usage:
            kwargs["usage_details"] = usage
        if cost:
            kwargs["cost_details"] = cost
        if model:
            kwargs["model"] = model
        if kwargs:
            self._obj.update(**kwargs)


class LangfuseTracer(Tracer):
    def __init__(self, client: Any) -> None:
        self._client = client

    @contextlib.contextmanager
    def observation(
        self,
        name: str,
        *,
        as_type: str = "span",
        model: str | None = None,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
        model_parameters: dict[str, Any] | None = None,
    ) -> Iterator[SpanHandle]:
        kwargs: dict[str, Any] = {"name": name, "as_type": as_type}
        if input is not None:
            kwargs["input"] = input
        if metadata:
            kwargs["metadata"] = metadata
        if as_type == "generation" and model:
            kwargs["model"] = model
        if as_type == "generation" and model_parameters:
            kwargs["model_parameters"] = model_parameters
        with self._client.start_as_current_observation(**kwargs) as obj:
            yield _LangfuseSpan(obj)

    def set_trace_io(self, *, input: Any | None = None, output: Any | None = None) -> None:
        with contextlib.suppress(Exception):
            self._client.set_current_trace_io(input=input, output=output)

    def current_trace_id(self) -> str | None:
        return self._client.get_current_trace_id()

    def flush(self) -> None:
        self._client.flush()

    def shutdown(self) -> None:
        self._client.shutdown()


def build_langfuse_tracer(settings: Settings) -> LangfuseTracer:
    """Build a real tracer, failing fast if credentials are missing or invalid."""
    settings.require_langfuse()
    from langfuse import Langfuse

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    if not client.auth_check():
        raise RuntimeError("Langfuse auth_check failed — bad credentials or host unreachable.")
    return LangfuseTracer(client)


# --------------------------------------------------------------------------- #
# In-memory tracer for tests: same interface, records everything.
# --------------------------------------------------------------------------- #


@dataclass
class SpanRecord:
    id: str
    trace_id: str
    name: str
    as_type: str
    parent_id: str | None = None
    input: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    output: Any | None = None
    usage: dict[str, int] | None = None
    cost: dict[str, float] | None = None


class _MemSpan(SpanHandle):
    def __init__(self, record: SpanRecord) -> None:
        self._record = record
        self.id = record.id
        self.trace_id = record.trace_id

    def update(
        self,
        *,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
        cost: dict[str, float] | None = None,
        model: str | None = None,
    ) -> None:
        if output is not None:
            self._record.output = output
        if metadata:
            self._record.metadata.update(metadata)
        # Mirror the real backend: usage/cost/model are only kept on generations.
        if self._record.as_type == "generation":
            if usage:
                self._record.usage = usage
            if cost:
                self._record.cost = cost
            if model:
                self._record.model = model


class InMemoryTracer(Tracer):
    def __init__(self, trace_id: str = "trace-test") -> None:
        self._trace_id = trace_id
        self.spans: list[SpanRecord] = []
        self.trace_io: dict[str, Any] = {}
        self._stack: list[str] = []
        self._n = 0

    @contextlib.contextmanager
    def observation(
        self,
        name: str,
        *,
        as_type: str = "span",
        model: str | None = None,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
        model_parameters: dict[str, Any] | None = None,
    ) -> Iterator[SpanHandle]:
        self._n += 1
        record = SpanRecord(
            id=f"obs-{self._n}",
            trace_id=self._trace_id,
            name=name,
            as_type=as_type,
            parent_id=self._stack[-1] if self._stack else None,
            input=input,
            metadata=dict(metadata or {}),
            model=model,
        )
        self.spans.append(record)
        self._stack.append(record.id)
        try:
            yield _MemSpan(record)
        finally:
            self._stack.pop()

    def set_trace_io(self, *, input: Any | None = None, output: Any | None = None) -> None:
        if input is not None:
            self.trace_io["input"] = input
        if output is not None:
            self.trace_io["output"] = output

    def current_trace_id(self) -> str | None:
        return self._trace_id if self._stack else None

    def flush(self) -> None:  # nothing to flush in memory
        pass

    def shutdown(self) -> None:
        pass

    # test helpers
    def by_name(self, name: str) -> list[SpanRecord]:
        return [s for s in self.spans if s.name == name]

    def generations(self) -> list[SpanRecord]:
        return [s for s in self.spans if s.as_type == "generation"]
