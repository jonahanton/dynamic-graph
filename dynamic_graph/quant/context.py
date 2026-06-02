from __future__ import annotations

import importlib.metadata
import importlib.util

from pydantic import BaseModel, Field

from dynamic_graph.domain.artifacts import Artifact
from dynamic_graph.domain.budget import Caps
from dynamic_graph.domain.questions import ForecastQuestion

# Approved scientific stack the quant agent may use: (import name, distribution).
APPROVED_PACKAGES: list[tuple[str, str]] = [
    ("duckdb", "duckdb"),
    ("polars", "polars"),
    ("pandas", "pandas"),
    ("numpy", "numpy"),
    ("scipy", "scipy"),
    ("statsmodels", "statsmodels"),
    ("sklearn", "scikit-learn"),
    ("lifelines", "lifelines"),
    ("pymc", "pymc"),
    ("arviz", "arviz"),
    ("matplotlib", "matplotlib"),
    ("plotly", "plotly"),
]


def available_packages() -> dict[str, str]:
    """Approved packages actually importable in this environment, with versions."""
    found: dict[str, str] = {}
    for import_name, dist in APPROVED_PACKAGES:
        if importlib.util.find_spec(import_name) is not None:
            try:
                found[dist] = importlib.metadata.version(dist)
            except importlib.metadata.PackageNotFoundError:
                found[dist] = "?"
    return found


class QuantContext(BaseModel):
    question_title: str
    resolution_criteria: str
    as_of: str
    workspace_dir: str
    available_packages: dict[str, str] = Field(default_factory=dict)
    input_artifacts: list[dict] = Field(default_factory=list)
    # Resource budget (rows is soft guidance; bytes/runtime enforced by the executor).
    max_rows: int = 0
    max_output_bytes: int = 0
    max_runtime_seconds: int = 0


def build_quant_context(
    question: ForecastQuestion,
    *,
    workspace_dir: str,
    artifacts: list[Artifact],
    caps: Caps,
) -> QuantContext:
    relevant = [
        {
            "id": a.id,
            "kind": a.kind,
            "summary": a.summary,
            "file_paths": a.file_paths,
            "payload": _trim_payload(a),
        }
        for a in artifacts
        if a.kind in ("source", "evidence", "dataset", "model_card", "data_quality")
    ]
    return QuantContext(
        question_title=question.title,
        resolution_criteria=question.resolution_criteria,
        as_of=question.as_of.isoformat(),
        workspace_dir=workspace_dir,
        available_packages=available_packages(),
        input_artifacts=relevant,
        max_rows=caps.max_quant_rows,
        max_output_bytes=caps.max_quant_bytes,
        max_runtime_seconds=caps.max_quant_runtime_seconds,
    )


def _trim_payload(artifact: Artifact) -> dict:
    """Keep the small, useful bits of an artifact payload for the agent prompt."""
    keep = ("url", "title", "claim", "value", "as_of", "published_at", "snippet")
    out = {k: v for k, v in artifact.payload.items() if k in keep}
    text = artifact.payload.get("text")
    if isinstance(text, str):
        out["text"] = text[:800]
    return out
