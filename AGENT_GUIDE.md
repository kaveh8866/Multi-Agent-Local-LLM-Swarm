# Agent Guide — Multi-Agent Local LLM Swarm

Operating contract and execution playbook for an AI coding agent (Cursor,
Claude Code, Windsurf or a terminal agent) picking up this repository. It states
what to run, in what order, what healthy looks like, how to inspect a run, and
what is verified versus assumed.

> **Read section 13 before making any availability or certification claim.**
> Stage 1 (scaffold, orchestration loop, local API) and Stage 2 (OpenTofu OCI
> modules, validated and planned) exist. **No cloud resource has been applied**
> and no model has been benchmarked. The human operator checklist lives in
> [`MANUAL_PREREQS.md`](MANUAL_PREREQS.md) — point the user there for credentials,
> keys, model licences and `.env`.

## 1. Current state

| Layer | Status | Evidence |
| --- | --- | --- |
| Repository scaffold, personas, workflows, task schema | Verified locally | `docs/stage-1-verification.txt` |
| Container topology (canonical + root entrypoint + GPU override) | `docker compose config -q` exit 0 | `docs/stage-1-verification.txt` |
| Orchestrator image build (182 MB, uid 10001) | Verified locally | `docs/stage-1-verification.txt` |
| Orchestration loop, tool gateway, budgets | 33 unit tests | `tests/test_scaffold.py` |
| Live multi-agent run on a local model | `completed`, 5 calls, 12.7 s (qwen2.5:0.5b) | `docs/stage-1-verification.txt` |
| OCI infrastructure modules | `fmt`, `validate`, read-only plan (8 to add) | `docs/stage-2-verification.txt` |
| OCI resources | **None applied** — no state file exists | — |
| Live cloud deployment | **None** | — |

## 2. What this project is

A local-only multi-agent system:

- **planner** decomposes an objective into at most `max_tasks` subtasks.
- **researcher**, **coder**, **critic** execute delegated stages (fan-out runs
  once per planned subtask).
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
| `MANUAL_PREREQS.md` | Human operator checklist: credentials, SSH key, volume, models, `.env` |
| `docker/docker-compose.yml` | Canonical topology, limits, hardening |
| `docker-compose.yml` (root) | `extends` entrypoint (declares the inherited volumes) |
| `docker/worker/Dockerfile` | Orchestrator image, uid 10001 |
| `docker/ollama/Modelfile.swarm` | Pinned sampling parameters, swarm preamble |
| `docker/compose.vllm.yaml` | GPU override (syntax-validated only, never executed) |
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
| `terraform/` | OpenTofu OCI modules (network, compute, storage) + cloud-init |
| `docs/stage-1-verification.txt`, `docs/stage-2-verification.txt` | Measured stage evidence |

## 4. Preflight (run before touching anything)

```bash
git status --porcelain                  # must be empty before you start
git log --oneline -3                    # know the baseline
python --version                        # 3.11+
python -c "import yaml; print(yaml.__version__)"
docker version --format '{{.Server.Version}}'
docker compose version
python -m unittest discover -s tests -q # 33 tests

# IaC (OpenTofu >= 1.6; this workspace ships it in .cloudguard/tools/tofu)
cd terraform && tofu fmt -recursive -check && tofu validate && cd ..
```

Capacity to confirm before pulling a model:

```bash
docker system df                        # image + volume sizes
free -h                                 # host RAM (Linux) / Task Manager on Windows
docker info --format '{{.MemTotal}}'    # Docker VM memory limit
```

If the tree is dirty, stop and report it. Do not "clean up" someone else's work.

## 5. Local bring-up — step by step

### 5.1 Environment

```bash
cp .env.example .env
# set a real local token; never reuse the placeholder
python -c "import secrets; print(secrets.token_hex(32))"
# paste it as SWARM_ORCHESTRATOR_TOKEN in .env
chmod 600 .env            # Linux/macOS; Windows: icacls .env /inheritance:r /grant:r "%USERNAME%:R"
git check-ignore -v .env  # must print the .gitignore rule
```

### 5.2 Validate and start

```bash
docker compose --env-file .env -f docker/docker-compose.yml config -q
docker compose --env-file .env -f docker/docker-compose.yml build swarm-orchestrator
docker compose --env-file .env -f docker/docker-compose.yml up -d
docker compose --env-file .env -f docker/docker-compose.yml ps
```

Expected: `llm-runtime` and `swarm-orchestrator` both `running ... (healthy)`.
The orchestrator health check runs `python -m swarm --healthcheck`; the
inference health check runs `ollama list`.

**Port conflicts.** If 11434 is already bound (a native Ollama install is
common), set `OLLAMA_PORT=11435` in `.env`. If 8090 is taken, set `SWARM_PORT`.
The in-network URL `SWARM_LLM_BASE_URL=http://llm-runtime:11434` does not change:
only the host binding moves.

### 5.3 Model weights into the persistent volume

Weights are pulled **inside** the container and land in the
`cloudguard-llm-swarm-weights` named volume — never in the checkout.

```bash
docker compose --env-file .env -f docker/docker-compose.yml \
  exec llm-runtime ollama pull qwen2.5:3b-instruct

docker compose --env-file .env -f docker/docker-compose.yml \
  exec llm-runtime ollama list

# optional: the pinned sampling profile and swarm system preamble
docker compose --env-file .env -f docker/docker-compose.yml \
  exec llm-runtime ollama create swarm-agent -f /config/Modelfile.swarm
# then set SWARM_LLM_MODEL=swarm-agent in .env and recreate the worker
```

Disk and volume evidence:

```bash
docker system df -v | grep -E 'cloudguard-llm-swarm|REPOSITORY'
docker run --rm -v cloudguard-llm-swarm-weights:/models alpine du -sh /models
```

### 5.4 Persistent storage on a cloud host (OCI, Stage 2 + Stage 5)

The block volume is mounted at `/mnt/llm-swarm` by cloud-init, which prepares
`models/`, `cache/` and `state/`. Verify the mount **before** pulling weights,
or the download lands on the boot disk and is lost on replacement:

```bash
findmnt /mnt/llm-swarm                       # must be the ext4 data volume
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT
systemctl status llm-swarm-mount --no-pager  # active (exited)
df -h /mnt/llm-swarm
```

**Cloud mount:** `docker/compose.oci.yaml` bind-mounts
`/mnt/llm-swarm/{models,state}` into the containers and replaces the named
volumes. Run Compose with both files on a host:

```bash
docker compose --env-file .env \
  -f docker/docker-compose.yml -f docker/compose.oci.yaml up -d --build
```

The host paths are `SWARM_HOST_MODEL_DIR` / `SWARM_HOST_STATE_DIR`. Do **not**
reuse `SWARM_STATE_DIR` for a host path: that variable is the worker's container
path (`/app/state`), and pointing it at a host directory moves the worker's
writes outside the bind mount. Verify consumption, not just attachment:

```bash
docker exec cg-swarm-llm du -sh /models        # matches df on /mnt/llm-swarm
df -h /mnt/llm-swarm
docker exec cg-swarm-orchestrator ls -ld /app/state
```

### 5.5 Health contract

```bash
curl -s http://127.0.0.1:8090/health   # 200 {"status":"ok",...}
curl -s http://127.0.0.1:8090/ready    # 200 when /v1/models answers, 503 otherwise
```

`/ready` 503 with `/health` 200 means the orchestrator is fine and the inference
server is not reachable yet (model loading or wrong URL). Wait and re-probe; do
not restart the worker.

Stop without losing weights or run records:

```bash
docker compose --env-file .env -f docker/docker-compose.yml down
```

`down -v` **destroys** the weights and state volumes. Ask the operator first.

## 6. Executing multi-agent collaboration tasks

```bash
# 0. no model needed: validate the workflow and render the delegation plan
PYTHONPATH=src python -m swarm --task configs/tasks/research-brief.json --dry-run
PYTHONPATH=src python -m swarm --list

# 1. live run through the API
TOKEN="$(grep '^SWARM_ORCHESTRATOR_TOKEN=' .env | cut -d= -f2-)"
curl -s http://127.0.0.1:8090/v1/tasks \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  --data @configs/tasks/research-brief.json | python -m json.tool

# 2. same task through the CLI (reads .env through the process environment)
PYTHONPATH=src python -m swarm --task configs/tasks/research-brief.json

# 3. full run record as JSON
PYTHONPATH=src python -m swarm --task configs/tasks/code-review.json --json \
  > runtime/code-review-run.json
```

HTTP semantics: `401` missing/wrong bearer token; `422` invalid task (the
message names the field); `413` body over 256 KiB; `404` unknown path.

Run statuses: `completed`, `completed_with_warnings` (non-fatal note, e.g. the
planner returned no parseable subtasks), `budget_exhausted`, `timeout`, `failed`
(the model call itself failed).

### 6.5 One-shot verification harness

`scripts/verify_local.py` performs the whole Stage 4 check in one command and
writes a JSON report to `runtime/` (gitignored):

```bash
python scripts/verify_local.py --concurrency 2 --max-steps 10
```

It measures `/health` and `/ready`, direct completions with token throughput,
every task in `configs/tasks/` at the given concurrency, per-container CPU/RAM
peaks, OOM state, unrendered prompt placeholders, and a log audit that fails if
the orchestrator token or any key-shaped string reaches stdout. `--max-steps` is
an upper bound: the effective budget stays `min(declared, cap)`. Exit 0 means
health 200/200, every completion 200 and every task completed.

The harness reads `OLLAMA_PORT` from `.env`. Port 11434 is often a *native*
Ollama on the workstation; querying it silently benchmarks the wrong server and
reports "model not found" for a perfectly healthy stack.

## 7. Inspecting agent communication

Every model call is one turn in the run record:

```bash
python - <<'PY'
import json
run = json.load(open("runtime/code-review-run.json", encoding="utf-8"))
print(run["run_id"], run["status"], run["steps"], f'{run["duration_ms"]} ms')
print("plan:", run["plan"])
for turn in run["turns"]:
    print(f'[{turn["stage"]}/{turn["persona_id"]}] {turn["duration_ms"]} ms')
    print("  instruction:", turn["instruction"].splitlines()[0][:100])
    print("  reply:", " ".join(turn["response"].split())[:160])
    for obs in turn["tool_observations"]:
        print("  tool:", obs)
print("errors:", run["errors"])
PY
```

What the fields prove:

- `plan` — what the planner actually returned after JSON parsing.
- `turns[].instruction` — the rendered prompt for that delegation, including the
  transcript appended by the orchestrator.
- `turns[].duration_ms` — orchestrator-measured per-call latency.
- `turns[].tool_observations` — `{tool, ok, result|error}`; a request the persona
  was not granted appears as `ok=false` instead of being executed.
- `errors` — budget, timeout, transport and planner-parse notes.

Request and server logs:

```bash
docker compose --env-file .env -f docker/docker-compose.yml \
  logs --tail=200 --timestamps swarm-orchestrator
docker compose --env-file .env -f docker/docker-compose.yml \
  logs --tail=200 --timestamps llm-runtime
```

The orchestrator logs method/path/status only — the `Authorization` header is
never logged. Ollama logs one `POST /v1/chat/completions` line per turn with its
own server-side duration; compare it with `turns[].duration_ms` to separate
network/overhead from inference time.

Persisted records: pass `SWARM_STATE_DIR` and write run JSON into the
`swarm-state` volume (`/app/state`). There is no built-in retention policy yet;
ship logs to a sink for production retention.

## 8. Monitoring CPU, GPU, memory and disk

```bash
# live per-container CPU / memory
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}'

# what the model is actually holding resident, and for how long
curl -s http://127.0.0.1:11434/api/ps | python -m json.tool

# limits and OOM history
docker inspect -f '{{.HostConfig.Memory}} {{.State.OOMKilled}} {{.State.RestartCount}}' cg-swarm-llm
docker inspect -f '{{.HostConfig.Memory}} {{.State.OOMKilled}}' cg-swarm-orchestrator

# host memory on the cloud node
free -h; vmstat 1 5

# weights volume growth
docker run --rm -v cloudguard-llm-swarm-weights:/models alpine df -h /models

# GPU (only with the vLLM override; the dev host has none)
docker compose --env-file .env -f docker/docker-compose.yml \
  -f docker/compose.vllm.yaml exec llm-runtime nvidia-smi
```

Sizing rules of thumb for a quantised model at 4k context (weights + KV cache +
runtime): ~1 GB for 0.5B, ~3 GB for 3B, ~7–9 GB for 8B. On the 24 GB A1 profile
a 3B model leaves ample headroom; an 8B model is tight and should be measured,
not assumed.

`OOMKilled=true` means the model does not fit `OLLAMA_MEM_LIMIT`: use a smaller
model or raise the limit deliberately. Never remove the limit to "fix" an OOM.

CPU throttling shows up as high `CPUPerc` against the container's `cpus` quota
with flat output. Keep `FASTEMBED`-style thread oversubscription out of this
stack: set `OLLAMA_NUM_PARALLEL` to the number of concurrent turns you actually
serve, not higher.

## 9. Debugging agent delegation workflows

Work from the cheapest signal to the most expensive:

1. **No model**: `--dry-run` proves the task parses, the workflow is wired and
   the delegation plan renders. If it fails here, nothing about inference
   matters — read the `422`/registry error.
2. **One step**: `"constraints": {"max_steps": 2}` isolates planning. If the run
   ends `budget_exhausted` at 2, the plan is simply larger than the budget.
3. **Plan shape**: inspect `plan[]`. Empty/one-item plan with
   `completed_with_warnings` means the planner ignored the JSON array
   instruction. Fix the plan instruction in `configs/workflows.yaml`, lower the
   planner temperature (0.1), or pick a model that follows instructions.
4. **Delegation mapping**: `turns[].stage` and `persona_id` must match the
   workflow stages; a `fanout` stage produces one turn per planned subtask.
5. **Tool refusals**: `tool_observations[].ok=false` with "not enabled for this
   persona" means the tool is not granted in `configs/agents.yaml`; "not allowed
   by the task constraints" means the task's `allowed_tools` excludes it. Fix
   configuration, never the gateway.
6. **Transport**: `failed: inference server returned HTTP 404` is usually a
   model tag that is not pulled; `unreachable` is a URL/port mismatch;
   `empty completion` is a model or prompt pathology.
7. **Time**: `turns[].duration_ms` versus the Ollama log isolates inference from
   orchestration. `timeout` is `SWARM_TASK_TIMEOUT_SECONDS`; raise it only after
   measuring.

Shrink a reproduction:

```bash
PYTHONPATH=src python -m swarm --objective "two sentence test" \
  --workflow reasoning_chain --json | python -m json.tool
```

## 10. Adding a persona or workflow

1. Add the prompt under `configs/prompts/`.
2. Add the persona to `configs/agents.yaml` with `capabilities` and only the
   `tools` it may call (`calculator`, `text_stats`).
3. Reference it from a stage in `configs/workflows.yaml`.
4. Run `python -m unittest discover -s tests -q` — the registry validates prompt
   paths, capability presence, tool names and persona references.

Rules:

- The first stage of a workflow must run the workflow `entry` persona.
- Keep `max_steps` ≤ 12 for interactive use; raise it with a measurement.
- `max_agents` is a registry ceiling, not a licence to add unused personas.
- Never add a tool that shells out, reads arbitrary paths or performs network
  I/O. The gateway is the security boundary.

## 11. Configuration rules

- `SWARM_MAX_STEPS`, workflow `max_steps` and task `constraints.max_steps` are
  combined with `min`; a task can only lower the budget.
- `SWARM_LLM_BASE_URL` may include `/v1`; the client normalises it.
- `SWARM_ORCHESTRATOR_TOKEN` must be non-empty or the API refuses to start.
  Rotate it by editing `.env` and recreating the worker; never commit it.
- Per-persona temperature overrides `SWARM_TEMPERATURE`; planner and critic stay
  near 0.1.
- Raising `max_tasks` multiplies model calls linearly.
- Resource limits live in `.env` and the compose file; do not remove
  `security_opt`, `cap_drop`, `read_only` or log rotation to make a test pass.

## 12. Failure playbook

| Symptom | Cause | Action |
| --- | --- | --- |
| `configuration error: SWARM_ORCHESTRATOR_TOKEN is not set` | `.env` missing or empty token | `cp .env.example .env`, set a token |
| `401` from `/v1/tasks` | wrong/missing bearer token | compare the request header with `.env` |
| `/ready` 503, `/health` 200 | inference down or model loading | `logs llm-runtime`, wait, `ollama list` |
| `budget_exhausted` | budget too low for the workflow | raise `max_steps` deliberately or simplify |
| `failed: inference server is unreachable` | URL/port/tag mismatch | check `SWARM_LLM_BASE_URL`, `docker compose ps` |
| `HTTP 404` from the model call | model tag not pulled | `ollama pull <tag>` or fix `SWARM_LLM_MODEL` |
| planner returns no parseable subtasks | model ignored the JSON instruction | run ends `completed_with_warnings`; tighten the plan instruction |
| tool observation `ok=false` | tool not granted or bad arguments | fix `configs/agents.yaml` or the prompt |
| container exits with `read_only file system` | orchestrator writing outside `/tmp` or `/app/state` | add a named volume; do not drop `read_only` |
| `OOMKilled=true` on `cg-swarm-llm` | model does not fit the limit | smaller model or raised `OLLAMA_MEM_LIMIT` |
| weights missing after restart | volume removed (`down -v`) or mount not on the data volume | re-pull; check `findmnt /mnt/llm-swarm` |
| mount unit `failed` after reboot while `findmnt` shows the volume | fstab mounted it first and device discovery skips already-mounted disks (fixed in the template) | install the current helper, `systemctl reset-failed llm-swarm-mount`, restart it |
| `port is already allocated` on 11434 | native Ollama or another container | set `OLLAMA_PORT=11435` in `.env` |

## 13. Cloud portability and certification scope

The IaC and the container configuration are **cloud-agnostic**. The same Compose
topology runs on AWS (EC2 + EBS + VPC), GCP (GCE + Persistent Disk + VPC) or OCI
(Ampere A1.Flex + Block Volume + VCN); only `terraform/provider.tf` and the three
module bodies change, while variables, outputs and the compose contract stay
fixed. `terraform/README.md` holds the mapping table.

**Status.** Stage 5 is deployed: `VM.Standard.A2.Flex` (4 OCPU / 24 GB, Ampere),
200 GB block volume at `/mnt/llm-swarm`, orchestrator and Ollama healthy, and all
three workflows completed on the host (`docs/stage-5-verification.txt`). The
default Always Free A1 shape was unavailable in all three Frankfurt ADs (45
attempts, `500-InternalError`), so the host is credit-funded — say that when
describing cost. The stack is not highly available, has no TLS or per-user auth,
and runs a 3B model on CPU at ~7 tok/s: do not call it production-ready or claim
GPU/cloud throughput beyond that measurement.

**Stage 6 tore it down.** Both stacks were stopped, all eight resources were
destroyed, `tofu state list` is empty and a read-only provider inventory reports
zero live project-owned resources (`docs/stage-6-verification.txt`). The Stage 5
numbers are historical measurements of a deployment that no longer exists: never
present them as a live endpoint, and re-apply before any new live check.

**Certification scope.** Final production verification and testing certification
for this project are performed strictly on **Oracle Cloud Infrastructure**, using
trial credits with high-RAM **Ampere ARM** instances (`VM.Standard.A1.Flex`,
4 OCPU / 24 GB) and persistent **OCI Block Volume** storage. AWS and GCP are
supported ports, not certified targets: no AWS or GCP certification run is made
or implied.

## 14. Git and evidence discipline

- Verify the boundary with `git check-ignore -v <path>` and
  `git status --porcelain`; never by eye.
- Never commit `.env`, model weights, state, keys, tfvars or logs.
- Commit messages describe why; no attribution trailers.
- A stage is complete only when its measured output is in
  `docs/stage-N-verification.txt` and committed.
- Report commands and their real output. `config -q` proves syntax, not
  behaviour; a local run proves this machine, not the cloud.

## 15. Stage roadmap

| Stage | Deliverable | Exit criteria |
| --- | --- | --- |
| 1 | Scaffold, compose, personas/workflows, task schema, run loop | tests, compose config, image build, API surface — **done** (`docs/stage-1-verification.txt`) |
| 2 | OCI network, inference node, block volume, cloud-init | `fmt`, `validate`, `plan` — **done** (`docs/stage-2-verification.txt`) |
| 3 | Agent guide and manual prerequisites | both documents complete, links valid, committed — **done** |
| 4 | Local evaluation: task success, latency, memory | 3/3 workflows completed on `qwen2.5:3b-instruct`, measured latency/throughput/CPU/RAM, clean log audit — **done** (`docs/stage-4-verification.txt`) |
| 5 | Live OCI deployment | healthy host, block volume consumed by the stack, 3/3 workflows on the host — **done** (`docs/stage-5-verification.txt`) |
| 6 | Clean teardown and zero-resource verification | stacks down, `tofu destroy` 8/8, empty state, provider inventory clean — **done** (`docs/stage-6-verification.txt`) |
| 7 | Security audit, documentation, cost model | zero-leak audit signed off, README and cost table published — **done** (`docs/stage-7-security-audit.txt`) |
| 8 | Scale and recovery | load test, restart/recovery drill |

> Distributed fan-out and queueing across nodes was originally pencilled in as
> Stage 3; it moved to Stage 8 (scale) so the documentation stage keeps its
> original place in the roadmap.

## 16. Handoff checklist

```bash
git status --porcelain                  # clean
python -m unittest discover -s tests -q # 33 tests
docker compose --env-file .env -f docker/docker-compose.yml config -q
cd terraform && tofu fmt -recursive -check && tofu validate && cd ..
git check-ignore -v .env terraform/terraform.tfvars terraform/.terraform
```

State what you actually ran, what you did not run, and which stage evidence file
covers it.
