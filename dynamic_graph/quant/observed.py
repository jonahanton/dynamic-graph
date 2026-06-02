from __future__ import annotations

from dynamic_graph.domain.artifacts import Artifact
from dynamic_graph.observability.runtime import ObservedRuntime
from dynamic_graph.quant.context import QuantContext, build_quant_context
from dynamic_graph.quant.executor import QuantExecutionResult, run_analysis
from dynamic_graph.quant.workspace import QuantWorkspace, WorkspaceArtifact


class ObservedQuant:
    """The only way agents reach the quant workbench. Writes are observed
    workspace spans; execution is charged against caps and observed."""

    def __init__(self, runtime: ObservedRuntime) -> None:
        self._runtime = runtime
        self._quant_root = runtime.paths.workspace / "quant"

    def workspace(self, node_id: str) -> QuantWorkspace:
        return QuantWorkspace(self._quant_root, node_id)

    def context(self, workspace: QuantWorkspace, artifacts: list[Artifact]) -> QuantContext:
        return build_quant_context(
            self._runtime.question,
            workspace_dir=str(workspace.dir),
            artifacts=artifacts,
            caps=self._runtime.caps,
        )

    def write_analysis(
        self, *, actor: str, workspace: QuantWorkspace, code: str
    ) -> WorkspaceArtifact:
        filename = "analysis.py"
        with self._runtime.observe(
            kind="workspace_write",
            actor=actor,
            name=f"write:{filename}",
            input={"filename": filename, "bytes": len(code.encode())},
        ) as rec:
            art = workspace.write(filename, code)
            rec.set_output({"path": art.path, "content_hash": art.content_hash})
            rec.note(
                summary=f"wrote {filename} ({art.byte_count}B)",
                path=art.path,
                content_hash=art.content_hash,
                byte_count=art.byte_count,
            )
            return art

    async def execute(self, *, actor: str, workspace: QuantWorkspace) -> QuantExecutionResult:
        self._runtime.charge_quant()
        code_preview = workspace.analysis_path.read_text(encoding="utf-8")[:800]
        with self._runtime.observe(
            kind="quant_exec",
            actor=actor,
            name=f"quant_exec:{workspace.dir.name}",
            input={"code_preview": code_preview},
        ) as rec:
            result = await run_analysis(workspace, caps=self._runtime.caps)
            result.trace_id = rec.trace_id
            result.observation_id = rec.observation_id
            rec.set_output(
                {
                    "ok": result.ok,
                    "exit_code": result.exit_code,
                    "duration_s": result.duration_seconds,
                    "outputs": [o.filename for o in result.output_files],
                    "stdout_tail": result.stdout[-1500:],
                    "stderr_tail": result.stderr[-1500:],
                }
            )
            status = "ok" if result.ok else "FAIL"
            rec.note(
                summary=(
                    f"exec analysis.py -> {status} "
                    f"({result.duration_seconds}s, {len(result.output_files)} outputs)"
                ),
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                code_hash=result.code_hash,
                output_files=[o.dataset_id for o in result.output_files],
                package_versions=result.package_versions,
                error=result.error,
            )
            return result
