import asyncio

from dynamic_graph.examples import hormuz_question
from dynamic_graph.quant import DiagnosticCheck, ModelCard, ObservedQuant, QuantGap, to_artifact
from tests.fakes import _ANALYSIS_CODE, offline_runtime


def test_workbench_executes_and_outputs(tmp_path):
    runtime = offline_runtime(tmp_path, hormuz_question())
    quant = ObservedQuant(runtime)
    workspace = quant.workspace("quant-base")

    async def go():
        with runtime.observe(kind="node", actor="quant-base"):
            quant.write_analysis(actor="quant-base", workspace=workspace, code=_ANALYSIS_CODE)
            return await quant.execute(actor="quant-base", workspace=workspace)

    result = asyncio.run(go())
    runtime.shutdown()

    assert result.ok and result.exit_code == 0
    assert result.output_files and result.output_files[0].filename == "result.json"
    assert result.package_versions  # captured stack versions
    assert workspace.analysis_path.exists()


def test_model_card_and_gap_register_as_artifacts():
    card = ModelCard(
        model_card_id="m1",
        created_by="quant-base",
        target_estimand="P(event)",
        method="beta-binomial base rate",
        headline_probability=0.34,
        diagnostics=[DiagnosticCheck(name="bounds", status="pass")],
        code_paths=["analysis.py"],
        code_hashes=["sha256:abc"],
    )
    artifact = to_artifact(card)
    assert artifact.kind == "model_card"
    assert artifact.payload["diagnostics"]
    assert "analysis.py" in artifact.file_paths

    sim = card.model_copy(update={"model_card_id": "m2", "n_draws": 5000})
    assert to_artifact(sim).kind == "simulation"

    gap = QuantGap(gap_id="g1", created_by="quant-base", missing="no series", needed_for="model")
    assert to_artifact(gap).kind == "quant_gap"
