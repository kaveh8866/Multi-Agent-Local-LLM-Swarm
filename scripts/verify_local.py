#!/usr/bin/env python3
"""Stage 4 local verification harness for the multi-agent swarm.

Runs against an already-started Compose stack and measures, rather than assumes:

  1. orchestrator and inference health (/health, /ready)
  2. direct OpenAI-compatible chat completions: latency and token throughput
  3. concurrent multi-agent tasks from configs/tasks/, with per-task status,
     step count, turn count and wall time
  4. container CPU/RAM sampled while the runs are in flight, plus OOM state
  5. a log audit proving the orchestrator token never reaches stdout

The harness reads the orchestrator token from .env (or the environment) and only
ever places it in an Authorization header. It never prints it, and the report it
writes contains no credential.

    python scripts/verify_local.py
    python scripts/verify_local.py --concurrency 2 --max-steps 5 --skip-agent
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "--env-file", ".env", "-f", "docker/docker-compose.yml"]
CONTAINERS = ("cg-swarm-llm", "cg-swarm-orchestrator")
LEAK_PATTERNS = {
    "openai_key": re.compile(r"\bsk-(?:proj-|live-|test-)[A-Za-z0-9_-]{16,}\b"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "ocid": re.compile(r"\bocid1\.[a-z0-9]+\.[a-z0-9-]*\.[a-z0-9]{16,}\b"),
}


def parse_env(path: Path) -> dict[str, str]:
    """KEY=VALUE parser for .env; comments and blanks ignored."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def http_json(
    url: str,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: float = 300.0,
) -> tuple[int, dict[str, Any], float]:
    """Return (status, body, elapsed_ms); HTTP errors are data, not exceptions."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            body = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            body = json.loads(exc.read() or b"{}")
        except json.JSONDecodeError:
            body = {"error": "non-json error body"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        status = 0
        body = {"error": str(getattr(exc, "reason", exc))}
    return status, body, round((time.monotonic() - started) * 1000, 1)


def docker_stats() -> dict[str, dict[str, float]]:
    """One CPU/RAM sample for the swarm containers via `docker stats`."""
    try:
        raw = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", *CONTAINERS],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return {}

    sample: dict[str, dict[str, float]] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = entry.get("Name", "")
        try:
            cpu = float(str(entry.get("CPUPerc", "0%")).rstrip("%") or 0)
        except ValueError:
            cpu = 0.0
        mem_usage = str(entry.get("MemUsage", "")).split("/")[0].strip()
        mem_mib = _to_mib(mem_usage)
        sample[name] = {"cpu_percent": cpu, "mem_mib": mem_mib}
    return sample


def _to_mib(value: str) -> float:
    match = re.match(r"([0-9.]+)\s*([KMGT]i?B)", value)
    if not match:
        return 0.0
    amount = float(match.group(1))
    unit = match.group(2)[0]
    factor = {"K": 1 / 1024, "M": 1.0, "G": 1024.0, "T": 1024.0 * 1024.0}.get(unit, 1.0)
    return round(amount * factor, 1)


class ResourceSampler:
    """Samples container CPU/RAM in the background while runs are in flight."""

    def __init__(self, interval: float = 2.0) -> None:
        self.interval = interval
        self.samples: list[dict[str, dict[str, float]]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> "ResourceSampler":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self.interval + 35)

    def _loop(self) -> None:
        while not self._stop.is_set():
            sample = docker_stats()
            if sample:
                self.samples.append(sample)
            self._stop.wait(self.interval)

    def summary(self) -> dict[str, Any]:
        peak_cpu = max((sum(c["cpu_percent"] for c in s.values()) for s in self.samples), default=0.0)
        peak_mem = max((sum(c["mem_mib"] for c in s.values()) for s in self.samples), default=0.0)
        return {
            "samples": len(self.samples),
            "peak_combined_cpu_percent": round(peak_cpu, 2),
            "peak_combined_mem_mib": round(peak_mem, 1),
            "peak_per_container": {
                name: {
                    "cpu_percent": max((s.get(name, {}).get("cpu_percent", 0.0) for s in self.samples), default=0.0),
                    "mem_mib": max((s.get(name, {}).get("mem_mib", 0.0) for s in self.samples), default=0.0),
                }
                for name in CONTAINERS
            },
        }


def compose_logs(service: str, lines: int = 400) -> str:
    try:
        return subprocess.run(
            [*COMPOSE, "logs", "--no-color", f"--tail={lines}", service],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def oom_state(container: str) -> dict[str, Any]:
    try:
        raw = subprocess.run(
            ["docker", "inspect", "-f", "{{json .State}}", container],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ).stdout.strip()
        state = json.loads(raw)
        return {"oom_killed": bool(state.get("OOMKilled")), "restarts": int(state.get("RestartCount", 0)), "status": state.get("Status")}
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return {"oom_killed": None, "restarts": None, "status": None}


def direct_inference(llm_url: str, model: str, runs: int) -> list[dict[str, Any]]:
    """Direct OpenAI-compatible completions with measured tokens/second."""
    prompt = "In two sentences, explain why a local inference server keeps model weights resident between requests."
    results: list[dict[str, Any]] = []
    for index in range(runs):
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 96,
            "stream": False,
        }
        status, body, elapsed = http_json(f"{llm_url}/v1/chat/completions", payload, timeout=300)
        usage = body.get("usage", {}) if isinstance(body, dict) else {}
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        results.append(
            {
                "run": index + 1,
                "status": status,
                "latency_ms": elapsed,
                "completion_tokens": completion_tokens,
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "tokens_per_second": round(completion_tokens / (elapsed / 1000), 2) if elapsed and completion_tokens else 0.0,
                "text": (body.get("choices", [{}])[0].get("message", {}).get("content", "") if status == 200 else ""),
                "error": body.get("error") if status != 200 else None,
            }
        )
    return results


def load_tasks(max_steps: int) -> list[dict[str, Any]]:
    """Load every task config; --max-steps caps, never raises, the declared budget."""
    tasks: list[dict[str, Any]] = []
    for path in sorted((ROOT / "configs" / "tasks").glob("*.json")):
        if path.name == "schema.json":
            continue
        task = json.loads(path.read_text(encoding="utf-8"))
        constraints = task.setdefault("constraints", {})
        declared = int(constraints.get("max_steps") or max_steps)
        constraints["max_steps"] = min(declared, max_steps)
        task["source"] = f"configs/tasks/{path.name}"
        tasks.append(task)
    return tasks


def run_agent_task(api_url: str, token: str, task: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    status, body, elapsed = http_json(f"{api_url}/v1/tasks", task, token=token, timeout=600)
    wall_ms = round((time.monotonic() - started) * 1000, 1)
    result = body.get("result", {}) if isinstance(body, dict) else {}
    turns = result.get("turns", [])
    return {
        "task_id": task.get("id"),
        "workflow": task.get("workflow"),
        "source": task.get("source"),
        "max_steps": (task.get("constraints") or {}).get("max_steps"),
        "http_status": status,
        "wall_ms": wall_ms,
        "api_elapsed_ms": elapsed,
        "status": result.get("status", "error"),
        "steps": result.get("steps", 0),
        "turns": len(turns),
        "plan": result.get("plan", []),
        "errors": result.get("errors", []),
        "tool_observations": [observation for turn in turns for observation in turn.get("tool_observations", [])],
        "turn_durations_ms": [turn.get("duration_ms", 0) for turn in turns],
        "turn_response_chars": [len((turn.get("response") or "").strip()) for turn in turns],
        "empty_responses": sum(1 for turn in turns if not (turn.get("response") or "").strip()),
        "personas_exercised": sorted({turn.get("persona_id", "") for turn in turns if turn.get("persona_id")}),
        "turns_detail": [
            {
                "stage": turn.get("stage"),
                "persona_id": turn.get("persona_id"),
                "duration_ms": turn.get("duration_ms", 0),
                "instruction_excerpt": _first_line(turn.get("instruction", ""), 120),
                # an unrendered template token in a rendered prompt is a bug
                "unrendered_placeholders": len(
                    re.findall(r"\{\{?\s*[a-z_][a-z0-9_]*\s*\}\}?", turn.get("instruction") or "")
                ),
            }
            for turn in turns
        ],
        # the full closing turn, whitespace-collapsed and bounded: this is the
        # deliverable the run produced, so it has to be auditable
        "final_reply": _collapse(turns[-1].get("response", "") if turns else "", 3000),
        "api_error": body.get("error") if status != 200 else None,
    }


def _collapse(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:limit]


def _first_line(text: str, limit: int) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:limit]
    return ""


def audit_logs(token: str, report_text: str) -> dict[str, Any]:
    logs = {service: compose_logs(service) for service in ("swarm-orchestrator", "llm-runtime")}
    combined = "\n".join(logs.values())
    findings: dict[str, int] = {name: len(pattern.findall(combined)) for name, pattern in LEAK_PATTERNS.items()}
    token_hits = combined.count(token) if token else 0
    authorization_header_hits = len(re.findall(r"(?i)authorization:\s*bearer", combined))
    return {
        "lines_scanned": {service: len(text.splitlines()) for service, text in logs.items()},
        "token_occurrences_in_logs": token_hits,
        "authorization_header_lines": authorization_header_hits,
        "secret_pattern_hits": findings,
        "token_in_report": bool(token and token in report_text),
        "clean": token_hits == 0 and authorization_header_hits == 0 and not any(findings.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the local multi-agent swarm stack")
    parser.add_argument("--env", default=".env", help="environment file (token is never printed)")
    parser.add_argument("--llm-url", default=None, help="inference server root (default: OLLAMA_PORT from .env)")
    parser.add_argument("--api-url", default="http://127.0.0.1:8090", help="orchestrator root")
    parser.add_argument("--model", default=None, help="model tag (default: SWARM_LLM_MODEL)")
    parser.add_argument("--concurrency", type=int, default=2, help="simultaneous agent runs")
    parser.add_argument("--max-steps", type=int, default=5, help="model-call budget per task")
    parser.add_argument("--inference-runs", type=int, default=2, help="direct completion samples")
    parser.add_argument("--skip-agent", action="store_true", help="health + inference only")
    parser.add_argument("--report", default="runtime/stage4-report.json", help="JSON report path")
    args = parser.parse_args(argv)

    env = parse_env(ROOT / args.env)
    token = os.environ.get("SWARM_ORCHESTRATOR_TOKEN") or env.get("SWARM_ORCHESTRATOR_TOKEN", "")
    model = args.model or env.get("SWARM_LLM_MODEL", "qwen2.5:3b-instruct")
    # The stack publishes on OLLAMA_PORT; 11434 is often a native Ollama on the
    # host, and querying it would silently benchmark the wrong server.
    args.llm_url = args.llm_url or f"http://127.0.0.1:{env.get('OLLAMA_PORT', '11434')}"

    if not token or token.startswith("replace-with"):
        print("error: SWARM_ORCHESTRATOR_TOKEN is missing or still the placeholder", file=sys.stderr)
        return 2

    print(f"model={model} concurrency={args.concurrency} max_steps={args.max_steps}")

    health_status, health_body, health_ms = http_json(f"{args.api_url}/health", timeout=15)
    ready_status, ready_body, ready_ms = http_json(f"{args.api_url}/ready", timeout=15)
    print(f"health={health_status} ({health_ms} ms) ready={ready_status} ({ready_ms} ms) llm={ready_body.get('llm')}")

    version_status, version_body, _ = http_json(f"{args.llm_url}/api/version", timeout=15)
    ps_status, ps_body, _ = http_json(f"{args.llm_url}/api/ps", timeout=15)
    loaded = [
        {
            "name": entry.get("name"),
            "size_bytes": entry.get("size"),
            "size_vram_bytes": entry.get("size_vram"),
            "expires_at": entry.get("expires_at"),
        }
        for entry in (ps_body.get("models", []) if isinstance(ps_body, dict) else [])
    ]
    print(f"llm_server: version={version_body.get('version')} loaded={[m['name'] for m in loaded]}")

    inference = direct_inference(args.llm_url, model, args.inference_runs)
    ok_inference = [run for run in inference if run["status"] == 200]
    latency = [run["latency_ms"] for run in ok_inference]
    throughput = [run["tokens_per_second"] for run in ok_inference if run["tokens_per_second"]]
    print(
        "inference: "
        + ", ".join(f"{run['latency_ms']} ms / {run['completion_tokens']} tok / {run['tokens_per_second']} tok/s" for run in inference)
    )

    agent_results: list[dict[str, Any]] = []
    resources: dict[str, Any] = {}
    agent_wall_ms = 0.0

    if not args.skip_agent:
        tasks = load_tasks(args.max_steps)
        sampler = ResourceSampler().start()
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            futures = [pool.submit(run_agent_task, args.api_url, token, task) for task in tasks]
            agent_results = [future.result() for future in futures]
        agent_wall_ms = round((time.monotonic() - started) * 1000, 1)
        sampler.stop()
        resources = sampler.summary()

        for result in agent_results:
            print(
                f"  {result['task_id']:<28} {result['workflow']:<16} http={result['http_status']} "
                f"status={result['status']:<24} steps={result['steps']} turns={result['turns']} wall={result['wall_ms']:.0f} ms"
            )

    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": model,
        "api_url": args.api_url,
        "llm_url": args.llm_url,
        "health": {"status": health_status, "latency_ms": health_ms, "body": health_body},
        "ready": {"status": ready_status, "latency_ms": ready_ms, "body": ready_body},
        "llm_server": {
            "version_status": version_status,
            "version": version_body.get("version") if isinstance(version_body, dict) else None,
            "ps_status": ps_status,
            "loaded_models": loaded,
        },
        "inference": {
            "runs": inference,
            "successful_runs": len(ok_inference),
            "median_latency_ms": round(statistics.median(latency), 1) if latency else None,
            "max_latency_ms": max(latency) if latency else None,
            "median_tokens_per_second": round(statistics.median(throughput), 2) if throughput else None,
        },
        "agent_runs": {
            "concurrency": args.concurrency,
            "max_steps": args.max_steps,
            "wall_ms": agent_wall_ms,
            "tasks": agent_results,
            "success_rate": round(
                sum(1 for result in agent_results if result["status"] in {"completed", "completed_with_warnings"})
                / len(agent_results),
                3,
            )
            if agent_results
            else None,
            "prompt_audit": {
                "turns_inspected": sum(result["turns"] for result in agent_results),
                "unrendered_placeholders": sum(
                    turn["unrendered_placeholders"]
                    for result in agent_results
                    for turn in result["turns_detail"]
                ),
                "personas_exercised": sorted(
                    {persona for result in agent_results for persona in result["personas_exercised"]}
                ),
                "tool_observations": sum(len(result["tool_observations"]) for result in agent_results),
            },
        },
        "resources": resources,
        "oom": {container: oom_state(container) for container in CONTAINERS},
    }

    serialized = json.dumps(report, indent=2)
    report["log_audit"] = audit_logs(token, serialized)

    out_path = ROOT / args.report
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print()
    print(f"success_rate={report['agent_runs']['success_rate']} wall={agent_wall_ms:.0f} ms")
    if resources:
        print(
            f"peak cpu={resources['peak_combined_cpu_percent']}% "
            f"peak mem={resources['peak_combined_mem_mib']} MiB over {resources['samples']} samples"
        )
    print(f"oom={ {name: value['oom_killed'] for name, value in report['oom'].items()} }")
    print(f"log_audit_clean={report['log_audit']['clean']} report={out_path}")

    healthy = health_status == 200 and ready_status == 200 and len(ok_inference) == len(inference)
    if agent_results:
        healthy = healthy and report["agent_runs"]["success_rate"] == 1.0
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
