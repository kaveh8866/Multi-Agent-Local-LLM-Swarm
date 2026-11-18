# Agent Guide — Multi-Agent Local LLM Swarm

Operating contract for an AI coding agent (Cursor, Claude Code, Windsurf or a
terminal agent) picking up this repository. It states what to run, in what
order, what healthy looks like, and what is verified versus assumed.

> **Read section 11 before making any availability or certification claim.**
> Only Stage 1 exists: the scaffold, the orchestration loop and the local API
> surface are verified. No cloud resource exists and no model was benchmarked.

## 1. Current state

| Layer | Status | Evidence |
| --- | --- | --- |
| Repository scaffold, personas, workflows, task schema | Verified locally | `docs/stage-1-verification.txt` |
| Container topology (canonical + root entrypoint + GPU override) | `docker compose config -q` exit 0 | `docs/stage-1-verification.txt` |
| Orchestrator image build | Verified locally | `docs/stage-1-verification.txt` |
| Orchestration loop, tool gateway, budgets | 31 unit tests | `tests/test_scaffold.py` |
| OCI infrastructure | **Not started** — `terraform/` is a contract only | — |
| Live deployment | **None** | — |

## 2. What this project is

A local-only multi-agent system:

- **planner** decomposes an objective into at most `max_tasks` subtasks.
- **researcher**, **coder**, **critic** execute delegated stages.
- The orchestrator enforces step, wall-clock and tool budgets and writes a run
  record with one turn per model call.
- Inference runs in the `llm-runtime` container (Ollama by default, vLLM
  optional); the orchestrator never calls a hosted API.

```
task JSON → validate → planner → delegated stages (single | fanout)
          → tool gateway (calculator, text_stats) → synthesis → run record
```

## 3. Repository map

| Path | Contents |
| --- | --- |
| `docker/docker-compose.yml` | Canonical topology, limits, hardening |
| `docker-compose.yml` (root) | `extends` entrypoint (declares the inherited volumes) |
| `docker/worker/Dockerfile` | Orchestrator image, uid 10001 |
| `docker/ollama/Modelfile.swarm` | Pinned sampling parameters, swarm preamble |
| `docker/compose.vllm.yaml` | GPU override (syntax-validated only) |
| `src/swarm/config.py` | Env settings and validation |
| `src/swarm/registry.py` | Persona, workflow and task loading |
| `src/swarm/delegation.py` | Plan parsing, capability routing, fan-out |
| `src/swarm/orchestrator.py` | Run loop, budgets, tool gateway, run record |
| `src/swarm/tools.py` | `calculator`, `text_stats`, request extraction |
| `src/swarm/llm.py` | OpenAI-compatible client and `ScriptedLLM` |
| `src/swarm/worker.py` | `/health`, `/ready`, `POST /v1/tasks` |
| `src/swarm/cli.py` | `python -m swarm` |
| `configs/` | Personas, prompts, workflows, task schema and examples |
| `tests/test_scaffold.py` | Unit suite (stdlib + PyYAML) |
| `terraform/` | Stage 2 contract — no `.tf` files yet |

## 4. Preflight

```bash
git status --porcelain                  # must be empty before you start
git log --oneline -3
python --version                        # 3.11+
python -c "import yaml; print(yaml.__version__)"
docker version --format '{{.Server.Version}}'
docker compose version
python -m unittest discover -s tests -q
```

If the tree is dirty, stop and report it. Do not "clean up" someone else's work.

## 5. Local bring-up

```bash
cp .env.example .env
docker compose --env-file .env -f docker/docker-compose.yml config -q
docker compose --env-file .env -f docker/docker-compose.yml up -d --build
```

First model pull (weights go into the `model-weights` volume, not the checkout):

```bash
docker compose --env-file .env -f docker/docker-compose.yml \
  exec llm-runtime ollama pull qwen2.5:3b-instruct
```

Health contract:

```bash
curl -s http://127.0.0.1:8090/health   # 200 {"status":"ok",...}
curl -s http://127.0.0.1:8090/ready    # 200 when /v1/models answers, 503 otherwise
```

`/ready` returning 503 while `/health` returns 200 means the orchestrator is
fine and the inference server is not yet reachable — wait for the model load,
do not restart the worker.

Stop without losing weights or run records:

```bash
docker compose --env-file .env -f docker/docker-compose.yml down
```

`down -v` **destroys** the model weights and agent state volumes. Ask the
operator before running it.

## 6. Running tasks

```bash
# validate a workflow without a model (safe anywhere)
PYTHONPATH=src python -m swarm --task configs/tasks/research-brief.json --dry-run

# list personas/workflows as loaded
PYTHONPATH=src python -m swarm --list

# live run through the API
TOKEN="$(grep '^SWARM_ORCHESTRATOR_TOKEN=' .env | cut -d= -f2-)"
curl -s http://127.0.0.1:8090/v1/tasks \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  --data @configs/tasks/research-brief.json

# same task through the CLI
PYTHONPATH=src python -m swarm --task configs/tasks/research-brief.json
```

A `401` means a missing or wrong token; `422` means the task is invalid (the
message names the field); `413` means the body exceeded 256 KiB.

Status meanings: `completed`, `completed_with_warnings` (a non-fatal note, for
example the planner returned no parseable subtasks), `budget_exhausted`,
`timeout`, `failed` (the model call itself failed).

## 7. Adding a persona or workflow

1. Add the prompt under `configs/prompts/`.
2. Add the persona to `configs/agents.yaml` with `capabilities` and only the
   `tools` it may call (`calculator`, `text_stats`).
3. Reference it from a stage in `configs/workflows.yaml`.
4. Run `python -m unittest discover -s tests -q` — the registry validates
   prompt paths, capability presence, tool names and persona references.

Rules:

- The first stage of a workflow must run the workflow `entry` persona.
- Keep `max_steps` ≤ 12 for interactive use; raise it with a measurement, not a
  guess.
- `max_agents` is a ceiling on the registry, not a licence to add personas that
  no workflow uses.
- Never add a tool that shells out, reads arbitrary paths or performs network
  I/O. The gateway is the security boundary; adding one bypasses it.

## 8. Monitoring

```bash
docker compose --env-file .env -f docker/docker-compose.yml ps
docker stats --no-stream cg-swarm-orchestrator cg-swarm-llm
docker inspect -f '{{.State.OOMKilled}}' cg-swarm-llm
docker compose --env-file .env -f docker/docker-compose.yml logs --tail=200 swarm-orchestrator
docker compose --env-file .env -f docker/docker-compose.yml logs --tail=200 llm-runtime
```

- `OOMKilled=true` on `cg-swarm-llm` means the model does not fit
  `OLLAMA_MEM_LIMIT`: lower the model size or raise the limit; do not remove the
  limit.
- Logs are bounded at 20 MiB × 3 files per container.
- The orchestrator logs method/path/status only; the `Authorization` header is
  never logged.
- Run records are returned by the API and can be persisted under
  `SWARM_STATE_DIR` (the `swarm-state` volume). There is no built-in retention
  policy yet — ship stdout to a sink for production retention.

## 9. Configuration rules

- `SWARM_MAX_STEPS`, workflow `max_steps` and task `constraints.max_steps` are
  combined with `min`; a task can only lower the budget, never raise it.
- `SWARM_LLM_BASE_URL` may include `/v1`; the client normalises it.
- `SWARM_ORCHESTRATOR_TOKEN` must be non-empty or the API refuses to start. Rotate
  it by editing `.env` and recreating the container; never commit it.
- Temperature per persona overrides `SWARM_TEMPERATURE`; planner and critic
  should stay near 0.1.
- Plans are capped by `max_tasks`; raising it multiplies model calls linearly.

## 10. Failure playbook

| Symptom | Cause | Action |
| --- | --- | --- |
| `configuration error: SWARM_ORCHESTRATOR_TOKEN is not set` | `.env` missing or empty token | `cp .env.example .env`, set a token |
| `401` from `/v1/tasks` | wrong/missing bearer token | compare `SWARM_ORCHESTRATOR_TOKEN` in the request and `.env` |
| `/ready` 503, `/health` 200 | inference server down or model loading | `logs llm-runtime`, wait, then `ollama list` |
| `budget_exhausted` | budget too low for the workflow | raise `max_steps` deliberately or simplify the task |
| `failed: inference server is unreachable` | network/port/tag mismatch | check `SWARM_LLM_BASE_URL`, `docker compose ps` |
| planner returns no parseable subtasks | model ignored the JSON instruction | run ends `completed_with_warnings`; tighten the plan instruction |
| tool observation `ok=false` | tool not granted or bad arguments | fix `configs/agents.yaml` or the prompt; do not widen the gateway |
| container exits with `read_only file system` | orchestrator writing outside `/tmp` or `/app/state` | add a named volume; do not drop `read_only` |

## 11. Cloud portability and certification scope

The container contract is cloud-agnostic. `terraform/` will implement OCI in
Stage 2; AWS and GCP are supported ports, not certified targets.

**Status.** Stage 1 is local only. No instance, network or volume exists. Do not
describe the project as deployed, highly available, benchmarked or
production-ready; stages 2–7 are the ones that earn those words.

**Certification scope.** Final production verification and testing
certification are performed strictly on **Oracle Cloud Infrastructure**, on
high-RAM Ampere ARM compute with persistent block storage.

## 12. Git and evidence discipline

- Verify the boundary with `git check-ignore -v <path>` and
  `git status --porcelain`; never by eye.
- Never commit `.env`, model weights, state, keys, tfvars or logs.
- Commit messages describe why; no attribution trailers.
- A stage is complete only when its measured output is in
  `docs/stage-N-verification.txt` and committed.
- Report commands and their real output. `config -q` proves syntax, not
  behaviour; a local run proves this machine, not the cloud.

## 13. Stage roadmap

| Stage | Deliverable | Exit criteria |
| --- | --- | --- |
| 1 | Scaffold, compose, personas/workflows, task schema, run loop | tests, compose config, image build, API surface — **done** |
| 2 | OCI network, inference node, block volume, cloud-init | `fmt`, `validate`, `plan` |
| 3 | Distributed fan-out and queueing | multi-node run record |
| 4 | Local evaluation: task success, latency, memory | measured metrics |
| 5 | Live OCI deployment | healthy host, live task run |
| 6 | Scale and recovery | load test, restart drill |
| 7 | Security audit, documentation, cost model | zero-leak audit signed off |

## 14. Handoff checklist

```bash
git status --porcelain                  # clean
python -m unittest discover -s tests -q # 31 tests
docker compose --env-file .env -f docker/docker-compose.yml config -q
git check-ignore -v .env
```

State what you actually ran, what you did not run, and which stage evidence file
covers it.
