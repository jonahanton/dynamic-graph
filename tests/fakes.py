"""Offline fakes: a scripted LLM client and a runtime/deps builder so the whole
dynamic graph runs without network or Langfuse."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from dynamic_graph.agents import AgentDeps
from dynamic_graph.connectors import FakeFetchClient, FakeSearchClient
from dynamic_graph.connectors.observed import ObservedWeb
from dynamic_graph.domain.budget import Caps
from dynamic_graph.domain.questions import ForecastQuestion
from dynamic_graph.llm import ObservedLLM
from dynamic_graph.llm.client import LLMClient, LLMResponse
from dynamic_graph.observability import build_runtime
from dynamic_graph.observability.langfuse import InMemoryTracer
from dynamic_graph.observability.runtime import ObservedRuntime
from dynamic_graph.quant import ObservedQuant

_SOURCE_URL = "https://www.reuters.com/world/example-article-2026"

_ANALYSIS_CODE = """\
import json
from pathlib import Path

alpha, beta = 1.0, 1.0
successes, trials = 3, 9
posterior_mean = (successes + alpha) / (trials + alpha + beta)
print("base rate posterior mean", round(posterior_mean, 4))

Path("outputs").mkdir(exist_ok=True)
Path("outputs/result.json").write_text(
    json.dumps({"p": posterior_mean, "successes": successes, "trials": trials})
)
"""


class FakeLLMClient(LLMClient):
    """Returns valid structured data per schema, varying the planner by call count."""

    provider = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.counts: dict[str, int] = defaultdict(int)

    async def complete(
        self,
        *,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        system: str | None = None,
        max_tokens: int = 900,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.counts[schema_name] += 1
        data = self._data(schema_name, self.counts[schema_name])
        return LLMResponse(
            data=data,
            text=json.dumps(data),
            model=self.model,
            provider=self.provider,
            usage={"input": 50, "output": 25, "cached_input": 0},
            response_id=f"fake-{schema_name}-{self.counts[schema_name]}",
        )

    def _data(self, name: str, n: int) -> dict[str, Any]:
        if name == "PlannerDecision":
            return self._planner(n)
        if name == "SearchPlan":
            return {
                "rationale": "find the current transit rate",
                "queries": [
                    {
                        "query": "Strait of Hormuz transits 7 day average",
                        "provider": "any",
                        "freshness": "none",
                    }
                ],
            }
        if name == "ResearchOutput":
            return {
                "summary": "Traffic remains below the threshold as of the as_of date.",
                "evidence": [
                    {
                        "claim": "Daily transits are running near 70 on a 7-day average.",
                        "stance": "supports_no",
                        "strength": "moderate",
                        "source_url": _SOURCE_URL,
                        "quote": "transits averaged about 70 per day over the past week",
                    }
                ],
                "signals": [
                    {
                        "kind": "missing_data",
                        "description": "No direct daily transit-count time series was extracted.",
                        "suggested_node_kind": "quant",
                    }
                ],
            }
        if name == "QuantPlan":
            return {
                "decision": "model",
                "approach": "beta-binomial base rate over recovery episodes",
                "analysis_code": _ANALYSIS_CODE,
                "gap_missing": "",
                "gap_needed_for": "",
                "gap_suggested_search": "",
            }
        if name == "QuantReport":
            return {
                "is_gap": False,
                "target_estimand": "P(>=100 transits/day 7dma by deadline)",
                "method": "beta-binomial base rate over recovery episodes",
                "findings": "Posterior mean recovery probability is about 0.34.",
                "confidence": "moderate",
                "headline_probability": 0.34,
                "interval_low": 0.2,
                "interval_high": 0.5,
                "estimates": [
                    {
                        "name": "posterior_mean",
                        "value": 0.34,
                        "interval_low": 0.2,
                        "interval_high": 0.5,
                        "note": "beta-binomial",
                    }
                ],
                "assumptions": ["recovery episodes are exchangeable"],
                "priors": ["alpha=1", "beta=1"],
                "diagnostics": [
                    {
                        "name": "posterior_mean",
                        "status": "pass",
                        "detail": "0.34",
                        "observed": 0.34,
                        "threshold": None,
                    }
                ],
                "sensitivity": [
                    {
                        "name": "prior",
                        "status": "pass",
                        "detail": "robust to Jeffreys prior",
                        "observed": None,
                        "threshold": None,
                    }
                ],
                "failure_modes": ["small sample"],
                "forecast_contribution": "anchor probability",
                "n_draws": 0,
                "gap_missing": "",
                "gap_needed_for": "",
            }
        if name == "Critique":
            return {
                "summary": "Main uncertainty is the live numeric average.",
                "cruxes": [
                    {
                        "description": "Current 7-day transit average is unresolved.",
                        "critical": False,
                    }
                ],
                "signals": [
                    {
                        "kind": "follow_up_search",
                        "description": "Find an official maritime transit dashboard.",
                        "suggested_node_kind": "research",
                    }
                ],
            }
        if name == "ForecastOutput":
            return {
                "probability": 0.34,
                "rationale": "Base rate anchors near 0.34; current state evidence supports NO.",
                "key_drivers": ["base rate", "current transit level"],
            }
        raise AssertionError(f"unscripted schema {name}")

    def _planner(self, n: int) -> dict[str, Any]:
        if n == 1:
            return {
                "rationale": "Expand the seed into research, quant and critic nodes.",
                "add_nodes": [
                    {
                        "node_id": "research-current",
                        "kind": "research",
                        "objective": "Find current-state evidence and the resolution source.",
                        "brief": "Find the latest 7-day transit average and how it resolves.",
                        "depends_on": ["seed"],
                        "input_artifact_ids": [],
                    },
                    {
                        "node_id": "quant-base",
                        "kind": "quant",
                        "objective": "Build a base-rate model of the resolution probability.",
                        "brief": "Use the research artefacts for a base-rate model or a gap.",
                        "depends_on": ["research-current"],
                        "input_artifact_ids": [],
                    },
                    {
                        "node_id": "critic",
                        "kind": "critic",
                        "objective": "Surface cruxes that would move the forecast.",
                        "brief": "Identify the decisive open cruxes and any leakage concerns.",
                        "depends_on": ["research-current", "quant-base"],
                        "input_artifact_ids": [],
                    },
                ],
                "cancel_node_ids": [],
                "stop": False,
                "addressed_signal_ids": [],
                "ignored_signals_note": "",
            }
        return {
            "rationale": "Evidence and quant are sufficient; add the terminal forecast path.",
            "add_nodes": [
                {
                    "node_id": "forecast",
                    "kind": "forecast",
                    "objective": "Produce the calibrated probability.",
                    "brief": "Weigh the base rate against current evidence and the open crux.",
                    "depends_on": ["research-current", "quant-base", "critic"],
                    "input_artifact_ids": [],
                },
                {
                    "node_id": "validate",
                    "kind": "validate",
                    "objective": "Validate and finalise the forecast.",
                    "brief": "Run final acceptance checks and finalise.",
                    "depends_on": ["forecast"],
                    "input_artifact_ids": [],
                },
            ],
            "cancel_node_ids": [],
            "stop": True,
            "addressed_signal_ids": [],
            "ignored_signals_note": "Lower-value signals deferred to keep the graph small.",
        }


def offline_runtime(
    tmp_path: Path, question: ForecastQuestion, *, caps: Caps | None = None
) -> ObservedRuntime:
    return build_runtime(
        question=question,
        caps=caps or Caps.small(),
        tracer=InMemoryTracer(),
        runs_root=tmp_path,
        run_id="test-run",
    )


def offline_deps(runtime: ObservedRuntime) -> AgentDeps:
    web = ObservedWeb(
        runtime=runtime,
        brave=FakeSearchClient(provider="brave"),
        fetch=FakeFetchClient(),
    )
    return AgentDeps(
        runtime=runtime,
        llm=ObservedLLM(FakeLLMClient(), runtime),
        web=web,
        quant=ObservedQuant(runtime),
        question=runtime.question,
    )
