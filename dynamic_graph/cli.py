from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import httpx
from rich.console import Console

from dynamic_graph.agents import AgentDeps
from dynamic_graph.config.settings import load_settings
from dynamic_graph.connectors import build_web
from dynamic_graph.domain.budget import Caps
from dynamic_graph.examples import question_from_text
from dynamic_graph.llm import ObservedLLM, build_llm
from dynamic_graph.observability import EventLog, build_runtime
from dynamic_graph.observability.langfuse import build_langfuse_tracer
from dynamic_graph.quant import ObservedQuant
from dynamic_graph.runtime import run_forecast

console = Console()

_KIND_STYLE = {
    "run": "bold white",
    "planner_signals": "magenta",
    "planner_fallback": "bold yellow",
    "graph_patch": "bold magenta",
    "reduce": "dim magenta",
    "research": "cyan",
    "web_search": "blue",
    "fetch": "blue",
    "quant": "yellow",
    "quant_exec": "bold yellow",
    "workspace_write": "yellow",
    "critic": "green",
    "forecast": "bold green",
    "validation": "bold red",
    "final_forecast": "bold white on blue",
    "llm_call": "dim",
}


async def _run(question_text: str, *, small: bool, runs_root: Path) -> None:
    settings = load_settings()
    settings.require_langfuse()
    settings.require_model_key()
    settings.require_search()

    question = question_from_text(question_text)
    caps = Caps.small() if small else Caps()
    tracer = build_langfuse_tracer(settings)
    runtime = build_runtime(question=question, caps=caps, tracer=tracer, runs_root=runs_root)

    console.print(f"[bold]run_id[/bold] = {runtime.run_id}")
    console.print(f"[dim]events: {runtime.paths.events}[/dim]")

    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
            llm_client = build_llm(settings, client=http)
            web = build_web(settings, runtime, client=http)
            deps = AgentDeps(
                runtime=runtime,
                llm=ObservedLLM(llm_client, runtime),
                web=web,
                quant=ObservedQuant(runtime),
                question=question,
            )
            try:
                final = await run_forecast(deps)
            finally:
                await llm_client.aclose()
    finally:
        runtime.shutdown()

    console.rule("[bold]forecast")
    if final is not None:
        console.print(f"[bold green]probability[/bold green] = {final.probability:.3f}")
        console.print(f"[bold]method[/bold] = {final.method}")
        console.print(f"[bold]rationale[/bold] = {final.rationale[:500]}")
        console.print(f"[dim]{final.calibration_note}[/dim]")
    else:
        console.print("[red]no final forecast produced[/red]")
    console.print(f"\n[dim]tail with: dynamic-graph tail --run-id {runtime.run_id}[/dim]")


def _tail(run_id: str, runs_root: Path) -> None:
    path = runs_root / run_id / "events.jsonl"
    if not path.exists():
        console.print(f"[red]no events at {path}[/red]")
        return
    for event in EventLog.read(path):
        style = _KIND_STYLE.get(event.kind, "white")
        label = f"{event.kind}:{event.actor}"
        console.print(f"[dim]{event.seq:>3}[/dim] [{style}]\\[{label}][/{style}] {event.summary}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="dynamic-graph", description="Dynamic forecasting graph.")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="run a dynamic forecast")
    run_parser.add_argument("--question", required=True)
    run_parser.add_argument("--small", action="store_true", help="tight caps for a tiny run")
    run_parser.add_argument("--runs-root", default="runs")

    tail_parser = sub.add_parser("tail", help="print a run's local event stream")
    tail_parser.add_argument("--run-id", required=True)
    tail_parser.add_argument("--runs-root", default="runs")

    args = parser.parse_args()
    if args.command == "run":
        asyncio.run(_run(args.question, small=args.small, runs_root=Path(args.runs_root)))
    elif args.command == "tail":
        _tail(args.run_id, Path(args.runs_root))


if __name__ == "__main__":
    main()
