"""Populate the configured Langfuse instance with one real trace from a fully
offline run (scripted LLM + fake web, real quant execution under caps). Verifies
the end-to-end Langfuse wiring without needing model or search API keys.

    uv run python scripts/langfuse_smoke.py

Requires LANGFUSE_* env vars (e.g. a local stack at http://localhost:3000).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dynamic_graph.config.settings import load_settings  # noqa: E402
from dynamic_graph.domain.budget import Caps  # noqa: E402
from dynamic_graph.examples import hormuz_question  # noqa: E402
from dynamic_graph.observability import build_runtime  # noqa: E402
from dynamic_graph.observability.langfuse import build_langfuse_tracer  # noqa: E402
from dynamic_graph.runtime import run_forecast  # noqa: E402
from tests.fakes import offline_deps  # noqa: E402


def main() -> None:
    settings = load_settings()
    settings.require_langfuse()  # fails closed if creds are missing
    tracer = build_langfuse_tracer(settings)

    question = hormuz_question()
    runtime = build_runtime(question=question, caps=Caps.small(), tracer=tracer)
    deps = offline_deps(runtime)

    final = asyncio.run(run_forecast(deps))
    runtime.shutdown()

    print(f"run_id:      {runtime.run_id}")
    print(f"probability: {final.probability if final else None}")
    print(f"events:      {runtime.paths.events}")
    print(f"View the trace in Langfuse at {settings.langfuse_host}")


if __name__ == "__main__":
    main()
