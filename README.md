# Multi-Agent Local LLM Swarm

![Python](https://img.shields.io/badge/orchestrator-Python%203.12-3776AB?logo=python&logoColor=white)
![Ollama](https://img.shields.io/badge/inference-Ollama%20%2F%20vLLM-000000?logo=ollama&logoColor=white)
![Docker](https://img.shields.io/badge/packaging-Docker%20Compose-2496ED?logo=docker&logoColor=white)
![OpenTofu](https://img.shields.io/badge/IaC-OpenTofu%20(Stage%202)-844FBA?logo=opentofu&logoColor=white)
![OCI](https://img.shields.io/badge/certified%20on-OCI-F80000?logo=oracle&logoColor=white)
![Stage](https://img.shields.io/badge/stage-4%20%C2%B7%20local%20verified-blue)
![License](https://img.shields.io/badge/license-MIT-green)

A containerized **multi-agent swarm** that runs entirely on local language
models: a planner decomposes a task, specialist agents (researcher, coder,
critic) execute the delegated subtasks, and a synthesis pass assembles the
result. Inference is served by a local Ollama or vLLM container; no request
leaves the node.

The design is deliberately small and inspectable: declared personas in YAML,
declared workflows, a two-tool whitelist, an explicit step/time/tool budget, and
a run record that says exactly which agent produced which turn. The same Compose
topology runs on a workstation and, from Stage 2, on OCI compute with a
persistent block volume for model weights and agent state.

> **Stage scope.** Stage 1 shipped the scaffold: repository layout, container
> topology, persona/workflow/task configuration, the orchestration loop and the
> task API, verified in [`docs/stage-1-verification.txt`](docs/stage-1-verification.txt).
> Stage 2 adds the OpenTofu OCI infrastructure contract (`terraform/`): it
> formats, validates and plans cleanly (8 resources to add) but has **not been
> applied**, so no cloud resource exists. Stage 3 adds the operator checklist and
> agent playbook; Stage 4 runs the stack for real on `qwen2.5:3b-instruct`
> (3/3 workflows completed, measured latency, CPU/RAM and a clean log audit —
> [`docs/stage-4-verification.txt`](docs/stage-4-verification.txt)). No
> model-quality claim is made — see [Honest limits](#honest-limits) and
> [`docs/stage-2-verification.txt`](docs/stage-2-verification.txt).

---

## Overview

| Layer | Choice | Why |
| --- | --- | --- |
| Inference | Ollama (`ollama/ollama:0.5.7`), optional vLLM GPU override | One OpenAI-compatible `/v1` contract; the same client drives CPU and GPU backends |
| Orchestration | Python 3.12, stdlib HTTP | No agent framework lock-in; the loop, budgets and tool gateway are readable in one file |
| Personas | `configs/agents.yaml` + prompt templates | Adding an agent is configuration, not code |
| Workflows | `configs/workflows.yaml` | Plan → fan-out → synthesis is data, so a workflow change cannot break the loop |
| Tools | `calculator`, `text_stats` | Deterministic, side-effect-free, AST-validated; no shell and no filesystem |
| Task contract | JSON Schema (`configs/tasks/schema.json`) | Tasks are validated before a model is called |
| Packaging | Docker Compose, hardened containers | Read-only orchestrator rootfs, `cap_drop: ALL`, `no-new-privileges`, resource envelopes |
| Infrastructure | OpenTofu (Stage 2) | OCI compute + block volume; the directory currently holds the contract only |

## Architecture

```
                     POST /v1/tasks  (Bearer orchestrator token)
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │  swarm-orchestrator :8080  │
                    │  ├ planner   (plan)        │
                    │  ├ researcher (fan-out)    │
                    │  ├ coder      (review)     │
                    │  ├ critic     (synthesis)  │
                    │  └ tool gateway            │
                    └─────────────┬──────────────┘
                                  │  /v1/chat/completions
                                  ▼
                    ┌────────────────────────────┐
                    │  llm-runtime (Ollama)      │
                    │  named volume: weights     │
                    └────────────────────────────┘

   named volumes: model-weights (never in Git) · swarm-state (run records)
   network: cloudguard-llm-swarm-net (bridge, loopback publishing only)
```

One run:

1. The task is validated against `configs/tasks/schema.json` semantics and the
   loaded workflow set.
2. The **planner** returns a JSON array of subtasks (bounded by
   `workflow.max_tasks`).
3. Each later stage either runs once (`mode: single`) or once per subtask
   (`mode: fanout`), with the transcript of prior turns appended.
4. A persona may request a tool with a fenced JSON object; the orchestrator
   executes it only if the persona grants the tool **and** the task allows it.
5. Step, wall-clock and tool budgets are enforced by the orchestrator; the run
   ends as `completed`, `completed_with_warnings`, `budget_exhausted`,
   `timeout` or `failed` — never silently truncated.

## Components

| Service | Image | Responsibility | Default bind |
| --- | --- | --- | --- |
| `llm-runtime` | `ollama/ollama:0.5.7` | Local inference, OpenAI-compatible `/v1` | `127.0.0.1:11434` |
| `swarm-orchestrator` | `cloudguard/llm-swarm:local` | Persona registry, delegation, tool gateway, task API | `127.0.0.1:8090` |

Both containers run with `cap_drop: ALL`, `no-new-privileges`, bounded pids and
20 MiB × 3 log rotation. The orchestrator additionally runs with a read-only
rootfs and a 64 MiB `tmpfs` for `/tmp`.

## Repository map

| Path | Contents |
| --- | --- |
| `docker/docker-compose.yml` | Canonical two-service topology, limits, hardening |
| `docker-compose.yml` (root) | `extends` entrypoint for discovery and audit |
| `docker/worker/Dockerfile` | Orchestrator image, unprivileged uid 10001 |
| `docker/ollama/Modelfile.swarm` | Optional pinned model parameters and system preamble |
| `docker/compose.vllm.yaml` | GPU override that swaps Ollama for vLLM |
| `src/swarm/config.py` | Environment settings, validation, token requirement |
| `src/swarm/registry.py` | Persona/workflow/task loading and validation |
| `src/swarm/delegation.py` | Plan parsing, capability routing, fan-out |
| `src/swarm/orchestrator.py` | The run loop, budgets, transcript, run record |
| `src/swarm/tools.py` | AST-validated calculator, text stats, tool gateway |
| `src/swarm/llm.py` | OpenAI-compatible client, probe, scripted test double |
| `src/swarm/worker.py` | `/health`, `/ready`, `POST /v1/tasks` |
| `src/swarm/cli.py` | `python -m swarm` — list, dry run, live run, healthcheck |
| `configs/agents.yaml` | Persona registry (prompt, capabilities, tool grants) |
| `configs/workflows.yaml` | `research_brief`, `code_review`, `reasoning_chain` |
| `configs/prompts/` | Persona prompts |
| `configs/tasks/` | JSON Schema + three example tasks |
| `terraform/` | OpenTofu OCI modules: VCN, security list, A1.Flex compute, block volume, cloud-init |
| `tests/test_scaffold.py` | 33 unit tests: config, registry, tools, delegation, loop, auth, compose |
| `scripts/verify_local.py` | Local verification harness: health, direct inference, concurrent agent runs, CPU/RAM sampling, log leak audit, JSON report |
| `MANUAL_PREREQS.md` | Operator checklist: OCI credentials, SSH key, volume, models, `.env` |
| `AGENT_GUIDE.md` | AI-agent playbook: bring-up, monitoring, delegation debugging |
| `docs/` | Stage evidence (`stage-1`, `stage-2`, `stage-3` and `stage-4` verification files) |

## Quickstart

```bash
cp .env.example .env                 # placeholders only; gitignored
docker compose --env-file .env -f docker/docker-compose.yml config -q
docker compose --env-file .env -f docker/docker-compose.yml up -d --build

# pull a model (weights land in the model-weights volume, never in the checkout)
docker compose --env-file .env -f docker/docker-compose.yml \
  exec llm-runtime ollama pull qwen2.5:3b-instruct

curl -s http://127.0.0.1:8090/health
curl -s http://127.0.0.1:8090/ready
```

Run a task:

```bash
TOKEN="$(grep '^SWARM_ORCHESTRATOR_TOKEN=' .env | cut -d= -f2-)"

curl -s http://127.0.0.1:8090/v1/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  --data @configs/tasks/research-brief.json
```

Without Docker, the configuration and the orchestration loop are testable
directly:

```bash
python -m unittest discover -s tests -v          # 33 tests
PYTHONPATH=src python -m swarm --list
PYTHONPATH=src python -m swarm --task configs/tasks/research-brief.json --dry-run
```

`--dry-run` validates the workflow and renders the delegation plan without
calling a model, so it is safe on a laptop with no weights.

Before deploying to a cloud host, work through
[`MANUAL_PREREQS.md`](MANUAL_PREREQS.md) (credentials, SSH key, block volume,
model weights, `.env`), then follow [`AGENT_GUIDE.md`](AGENT_GUIDE.md) for the
run, monitoring and delegation-debugging playbook.

## Task contract

```json
{
  "id": "research-local-llm-serving",
  "objective": "Compare Ollama and vLLM for serving a 3B model on a CPU-only node.",
  "workflow": "research_brief",
  "inputs": { "max_words": 400 },
  "constraints": { "max_steps": 8, "allowed_tools": ["text_stats"] }
}
```

- `objective` (required) — normalised to single spaces, max 4000 characters.
- `workflow` (required) — one of `research_brief`, `code_review`,
  `reasoning_chain`.
- `constraints.max_steps` — a *lower* bound on top of the workflow and global
  budgets; the effective budget is the minimum of the three.
- `constraints.allowed_tools` — narrows what any persona may call for this task.

The full schema is `configs/tasks/schema.json` (JSON Schema 2020-12).

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `SWARM_LLM_BASE_URL` | `http://127.0.0.1:11434` | Inference server; `/v1` is appended when absent |
| `SWARM_LLM_MODEL` | `qwen2.5:3b-instruct` | Model name sent in every completion |
| `SWARM_LLM_API_KEY` | empty | Optional Bearer key for a proxied endpoint |
| `SWARM_LLM_TIMEOUT_SECONDS` | `120` | Per-completion timeout |
| `SWARM_ORCHESTRATOR_TOKEN` | — | **Required**; the task API refuses to start without it |
| `SWARM_MAX_AGENTS` | `4` | Persona ceiling for the registry (validated 1–16) |
| `SWARM_MAX_STEPS` | `12` | Global model-call budget (validated 1–64) |
| `SWARM_TASK_TIMEOUT_SECONDS` | `300` | Wall-clock ceiling for one run |
| `SWARM_TEMPERATURE` | `0.2` | Default sampling temperature |
| `OLLAMA_MEM_LIMIT` / `OLLAMA_CPUS` | `12g` / `6` | Inference container envelope |
| `WORKER_MEM_LIMIT` / `WORKER_CPUS` | `2g` / `2` | Orchestrator envelope |

Secrets are read from the environment only. The process never opens `.env`,
never logs the token, and the container API answers `401` for a missing or
mismatched bearer token (constant-time comparison).

## Security posture

- **Zero-leak Git boundary** — `.env*`, keys, PEMs, model weights (`*.gguf`,
  `*.safetensors`, …), agent state, databases, logs, Terraform state and tfvars
  are ignored; only templates are tracked.
- **No tool escape** — the calculator parses with `ast` and evaluates a numeric
  whitelist; there is no `eval`, no subprocess, no filesystem read, no network
  call. Unknown tools, undeclared arguments and non-granted personas are
  refused and recorded as failed observations.
- **Bounded work** — step, wall-clock and `max_tasks` ceilings are enforced in
  the loop; a runaway plan cannot spawn unbounded model calls.
- **Least-privilege containers** — loopback publishing, `cap_drop: ALL`,
  `no-new-privileges`, read-only orchestrator rootfs, pids limits, log rotation.
- **No weights in Git** — model blobs live in the `model-weights` named volume;
  the Dockerfile bakes no weights.

## Measured locally (Stage 4)

`qwen2.5:3b-instruct` on CPU (Ollama 0.5.7, 6 CPU quota), two concurrent agent
runs, workstation with 16 logical CPUs — full evidence in
[`docs/stage-4-verification.txt`](docs/stage-4-verification.txt):

| Metric | Measured |
| --- | --- |
| Workflow success rate | 3/3 completed (`code_review`, `research_brief`, `reasoning_chain`), reproduced across two runs |
| Agent wall-clock | 46–218 s per task, 218 s total with concurrency 2 |
| Direct inference latency | 3.3–5.8 s per 50–71-token reply (median 4.7 s) |
| Token throughput | 12.3–15.4 tok/s (median 13.9), CPU only |
| Peak resources | 648% CPU across 6 cores, 4.1 GiB RAM, no OOM, 0 restarts |
| Prompt/handoff audit | 16 turns, 0 unrendered placeholders, all 4 personas, 0 empty replies |
| Secret audit | 0 token occurrences, 0 key-shaped strings in 416 log lines |

These are single-sample CPU numbers on one node; they are not a GPU, cloud or
multi-node benchmark.

## Honest limits

- **No cloud resource exists.** The OpenTofu modules format, validate and plan
  cleanly against the live tenancy (8 resources to add, 0 warnings), but nothing
  has been applied: no VCN, instance, volume or state file exists, and
  cloud-init has never run on a host.
- **Performance numbers are one model, one node, one sample each.** The unit
  suite still runs against a scripted model double; Stage 4 measured a CPU-only
  3B model and makes no claim about larger models, GPU throughput, concurrency
  beyond two runs, or variance across runs.
- **The code-review workflow cannot read source files.** The tool whitelist is
  `calculator`/`text_stats`, so the coder persona reviews from the objective
  text; its findings are generic and code-review recall is not claimed.
- **Run records are not persisted.** They are returned in the HTTP response and
  written to container logs, not to disk.
- **Single node, single process.** Fan-out is concurrent *within* one run loop
  today; distributing agents across nodes is Stage 6.
- **The GPU override is unexecuted.** `docker/compose.vllm.yaml` is
  syntax-validated only; the development host has no GPU.
- **No authentication beyond a shared bearer token.** Per-user identity and
  audit storage are out of scope for a loopback control plane.

## Cloud portability and certification scope

The topology is cloud-agnostic: the same Compose contract runs on AWS (EC2 +
EBS), GCP (GCE + Persistent Disk) or OCI (Ampere A1.Flex + Block Volume). The
variable and output contract in `terraform/` is provider neutral; porting
replaces `provider.tf` and the three module bodies only. The OCI implementation
is validated and planned; AWS and GCP are documented ports, not tested targets.

Final production verification and testing certification for this project are
performed strictly on **Oracle Cloud Infrastructure**, using trial credits with
high-RAM **Ampere ARM** instances and persistent **OCI Block Volume** storage.
AWS and GCP are supported ports, not certified targets.

## Stage roadmap

| Stage | Deliverable | Exit criteria |
| --- | --- | --- |
| 1 | Scaffold, Compose topology, personas, workflows, task schema, orchestration loop | unit tests, compose config, image build, local API surface — **done** (`docs/stage-1-verification.txt`) |
| 2 | OCI network, inference node, block volume, cloud-init | `fmt`, `validate`, `plan` — **done** (`docs/stage-2-verification.txt`), not applied |
| 3 | Agent guide and manual prerequisites | both documents complete, links valid — **done** |
| 4 | Local evaluation: task success, latency, memory | 3/3 workflows completed on 3B (CPU), measured latency/throughput/CPU/RAM, harness + log audit — **done** (`docs/stage-4-verification.txt`) |
| 5 | Live OCI deployment | cloud-init done, volume mounted, healthy services, live run |
| 6 | Scale and recovery (includes distributed fan-out and queueing across nodes) | load test, multi-node run record, restart drill |
| 7 | Security audit, documentation, cost model | zero-leak audit signed off |

## License

MIT — see [LICENSE](LICENSE).
