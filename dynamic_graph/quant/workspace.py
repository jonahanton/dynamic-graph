from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel


def content_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()[:16]


class WorkspaceArtifact(BaseModel):
    filename: str
    path: str
    content_hash: str
    byte_count: int


class QuantWorkspace:
    """The per-node quant directory layout:

    runs/<run_id>/workspace/quant/<node_id>/
      analysis.py
      inputs/
      outputs/
    """

    def __init__(self, quant_root: Path, node_id: str) -> None:
        safe = node_id.replace("/", "_")
        self.dir = quant_root / safe
        self.inputs = self.dir / "inputs"
        self.outputs = self.dir / "outputs"
        for d in (self.dir, self.inputs, self.outputs):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def analysis_path(self) -> Path:
        return self.dir / "analysis.py"

    def write(
        self, filename: str, content: str | bytes, *, in_inputs: bool = False
    ) -> WorkspaceArtifact:
        data = content.encode("utf-8") if isinstance(content, str) else content
        target = (self.inputs if in_inputs else self.dir) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return WorkspaceArtifact(
            filename=filename,
            path=str(target),
            content_hash=content_hash(data),
            byte_count=len(data),
        )

    def list_outputs(self) -> list[WorkspaceArtifact]:
        artifacts: list[WorkspaceArtifact] = []
        if not self.outputs.exists():
            return artifacts
        for path in sorted(self.outputs.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                artifacts.append(
                    WorkspaceArtifact(
                        filename=str(path.relative_to(self.outputs)),
                        path=str(path),
                        content_hash=content_hash(data),
                        byte_count=len(data),
                    )
                )
        return artifacts
