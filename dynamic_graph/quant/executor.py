from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import time

from pydantic import BaseModel, Field

from dynamic_graph.domain.budget import Caps
from dynamic_graph.quant.context import available_packages
from dynamic_graph.quant.workspace import QuantWorkspace, content_hash

_MAX_CAPTURE_CHARS = 20_000

# Hardens the child before running analysis.py: blocks the network and the
# process-spawn/exec surface and sets CPU/file-size rlimits. Best-effort for
# LLM-written code, not an adversary-proof jail (native code can still escape).
_RUNNER = """\
import os, runpy, socket

def _no_network(*args, **kwargs):
    raise OSError("network access is disabled inside the quant sandbox")

socket.socket.connect = _no_network
socket.socket.connect_ex = _no_network
socket.create_connection = _no_network

def _no_spawn(*args, **kwargs):
    raise OSError("spawning or replacing processes is disabled inside the quant sandbox")

for _name in (
    "system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp",
    "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp", "execlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe",
):
    if hasattr(os, _name):
        setattr(os, _name, _no_spawn)
try:
    import subprocess
    subprocess.Popen = _no_spawn
except Exception:
    pass

try:
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, ({cpu}, {cpu}))
    resource.setrlimit(resource.RLIMIT_FSIZE, ({fsize}, {fsize}))
except Exception:
    pass

runpy.run_path("analysis.py", run_name="__main__")
"""


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Kill the child's whole session so spawned grandchildren die with it."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        proc.kill()


class DataManifestEntry(BaseModel):
    dataset_id: str
    filename: str
    path: str
    content_hash: str
    byte_count: int


class QuantExecutionResult(BaseModel):
    ok: bool
    exit_code: int
    timed_out: bool = False
    duration_seconds: float
    stdout: str = ""
    stderr: str = ""
    code_path: str
    code_hash: str
    output_files: list[DataManifestEntry] = Field(default_factory=list)
    output_bytes: int = 0
    package_versions: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    trace_id: str | None = None
    observation_id: str | None = None


async def run_analysis(
    workspace: QuantWorkspace,
    *,
    caps: Caps,
    timeout_seconds: int | None = None,
) -> QuantExecutionResult:
    """Execute `analysis.py` in the node workspace under hard caps with no network."""
    if not workspace.analysis_path.exists():
        raise FileNotFoundError(f"no analysis.py at {workspace.analysis_path}")

    code = workspace.analysis_path.read_bytes()
    timeout = timeout_seconds or caps.max_quant_runtime_seconds
    runner_path = workspace.dir / "_runner.py"
    runner_path.write_text(
        _RUNNER.format(cpu=timeout + 5, fsize=caps.max_quant_bytes), encoding="utf-8"
    )

    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONUNBUFFERED": "1",
        "MPLBACKEND": "Agg",
    }
    if "VIRTUAL_ENV" in os.environ:
        env["VIRTUAL_ENV"] = os.environ["VIRTUAL_ENV"]

    started = time.monotonic()
    timed_out = False
    error: str | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "_runner.py",
            cwd=str(workspace.dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            exit_code = proc.returncode if proc.returncode is not None else -1
        except TimeoutError:
            timed_out = True
            _kill_process_group(proc)
            out_b, err_b = await proc.communicate()
            exit_code = -1
            error = f"timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001 - surface as a structured failure
        return QuantExecutionResult(
            ok=False,
            exit_code=-1,
            duration_seconds=round(time.monotonic() - started, 3),
            code_path=str(workspace.analysis_path),
            code_hash=content_hash(code),
            package_versions=available_packages(),
            error=f"{type(exc).__name__}: {exc}",
        )

    duration = round(time.monotonic() - started, 3)
    stdout = out_b.decode("utf-8", "replace")[:_MAX_CAPTURE_CHARS]
    stderr = err_b.decode("utf-8", "replace")[:_MAX_CAPTURE_CHARS]

    outputs = [
        DataManifestEntry(
            dataset_id=f"{workspace.dir.name}:{art.filename}",
            filename=art.filename,
            path=art.path,
            content_hash=art.content_hash,
            byte_count=art.byte_count,
        )
        for art in workspace.list_outputs()
    ]
    output_bytes = sum(o.byte_count for o in outputs)
    over_bytes = output_bytes > caps.max_quant_bytes
    if over_bytes:
        error = (error or "") + f" output exceeded max_quant_bytes ({caps.max_quant_bytes})"

    ok = exit_code == 0 and not timed_out and not over_bytes
    if not ok and error is None:
        error = f"exit code {exit_code}"

    return QuantExecutionResult(
        ok=ok,
        exit_code=exit_code,
        timed_out=timed_out,
        duration_seconds=duration,
        stdout=stdout,
        stderr=stderr,
        code_path=str(workspace.analysis_path),
        code_hash=content_hash(code),
        output_files=outputs,
        output_bytes=output_bytes,
        package_versions=available_packages(),
        error=error,
    )
