"""Command-line entry point.

    python -m swarm --list
    python -m swarm --task configs/tasks/research-brief.json --dry-run
    python -m swarm --objective "..." --workflow reasoning_chain
    python -m swarm --healthcheck          # used by the container healthcheck

Exit codes: 0 success, 2 configuration/usage error, 3 the run itself failed.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from . import VERSION
from .config import ConfigError, Settings
from .llm import LLMClient
from .orchestrator import Orchestrator, OrchestratorError, RunResult
from .registry import RegistryError, Task, load_personas, load_workflows


def _healthcheck(settings: Settings) -> int:
    url = f"http://127.0.0.1:{settings.server_port}/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - loopback only
            return 0 if response.status == 200 else 1
    except (urllib.error.URLError, OSError):
        return 1


def _summary(result: RunResult) -> str:
    lines = [
        f"run        {result.run_id}",
        f"workflow   {result.workflow}",
        f"status     {result.status}{' (dry run)' if result.dry_run else ''}",
        f"steps      {result.steps}",
        f"duration   {result.duration_ms} ms",
        f"objective  {result.objective}",
        "",
        "plan",
    ]
    lines.extend(f"  - {item}" for item in result.plan) if result.plan else lines.append("  (none)")
    lines.append("")
    lines.append("turns")
    for turn in result.turns:
        first_line = turn.response.strip().splitlines()[0] if turn.response.strip() else ""
        lines.append(f"  [{turn.stage}/{turn.persona_id}] {turn.duration_ms} ms :: {first_line[:120]}")
        for observation in turn.tool_observations:
            lines.append(f"      tool {observation.get('tool')} ok={observation.get('ok')}")
    if result.errors:
        lines.append("")
        lines.append("errors")
        lines.extend(f"  - {error}" for error in result.errors)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="swarm", description=f"Multi-agent local LLM swarm {VERSION}")
    parser.add_argument("--task", type=Path, help="path to a task JSON file")
    parser.add_argument("--objective", help="objective for an ad-hoc task")
    parser.add_argument("--workflow", default="research_brief", help="workflow id (default: research_brief)")
    parser.add_argument("--dry-run", action="store_true", help="validate the workflow without calling the model")
    parser.add_argument("--json", action="store_true", help="emit the full run record as JSON")
    parser.add_argument("--list", action="store_true", help="list personas, workflows and tools")
    parser.add_argument("--healthcheck", action="store_true", help="probe the local task API and exit")
    parser.add_argument("--config-dir", type=Path, help="override the configuration directory")
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
        if args.config_dir is not None:
            settings = replace(settings, config_dir=args.config_dir.resolve())

        if args.healthcheck:
            return _healthcheck(settings)

        personas = load_personas(settings.config_dir / "agents.yaml")
        workflows = load_workflows(settings.config_dir / "workflows.yaml", personas)

        if args.list:
            print(
                json.dumps(
                    {
                        "version": VERSION,
                        "model": settings.llm_model,
                        "personas": {pid: list(persona.capabilities) for pid, persona in sorted(personas.items())},
                        "workflows": {
                            wid: [f"{stage.id}:{stage.mode}:{stage.persona}" for stage in workflow.stages]
                            for wid, workflow in sorted(workflows.items())
                        },
                    },
                    indent=2,
                )
            )
            return 0

        if args.task is not None:
            payload = json.loads(args.task.read_text(encoding="utf-8"))
        elif args.objective:
            payload = {"objective": args.objective, "workflow": args.workflow}
        else:
            parser.error("either --task or --objective is required")

        task = Task.from_dict(payload, known_workflows=workflows)
        llm = None
        if not args.dry_run:
            llm = LLMClient(
                base_url=settings.llm_base_url,
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                timeout=settings.llm_timeout_seconds,
            )

        result = Orchestrator(settings, personas, workflows, llm).run(task, dry_run=args.dry_run)

        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(_summary(result))

        return 3 if result.status == "failed" else 0

    except (ConfigError, RegistryError, OrchestratorError, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
