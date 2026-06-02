from dynamic_graph.domain.artifacts import Artifact
from dynamic_graph.domain.forecasts import ForecastEstimate
from dynamic_graph.examples import hormuz_question
from dynamic_graph.validation import aggregate_probabilities, finalise_forecast


def test_aggregate_probabilities_pools_in_log_odds():
    assert aggregate_probabilities([]) == (0.5, "no_estimates_fallback")
    assert aggregate_probabilities([0.3]) == (0.3, "single_estimate")
    pooled, method = aggregate_probabilities([0.2, 0.8])
    assert method == "logit_pool" and abs(pooled - 0.5) < 1e-6  # symmetric odds
    same, _ = aggregate_probabilities([0.6, 0.6, 0.6])
    assert abs(same - 0.6) < 1e-6


def test_finalise_accepts_supported_forecast():
    question = hormuz_question()
    evidence = Artifact(id="e1", kind="evidence", created_by="research", summary="evidence")
    estimate = ForecastEstimate(
        id="est1", created_by="forecast", probability=0.34, rationale="reasoned", source_ids=["e1"]
    )
    final, report = finalise_forecast(
        question=question, estimates=[estimate], artifacts=[evidence], cruxes=[]
    )
    assert 0.0 <= final.probability <= 1.0
    assert final.method == "single_estimate"
    assert report.verdict in ("accepted", "accepted_with_warnings")


def test_finalise_rejects_unsupported_forecast():
    question = hormuz_question()
    final, report = finalise_forecast(question=question, estimates=[], artifacts=[], cruxes=[])
    assert report.verdict == "rejected"  # no source support, no estimates
    assert final.probability == 0.5
