<div align="center">

# 🤖 Multi-Agent Local LLM Swarm

**A self-hosted, containerized swarm of specialised LLM agents — planner, researcher, coder and critic — collaborating over an OpenAI-compatible local inference server. No prompt, token or transcript ever leaves the node.**

[![Python](https://img.shields.io/badge/orchestrator-Python%203.12-3772FF?logo=python&logoColor=white)](pyproject.toml)
[![Ollama](https://img.shields.io/badge/inference-Ollama%200.5.7-000000?logo=ollama&logoColor=white)](docker/docker-compose.yml)
[![vLLM](https://img.shields.io/badge/GPU%20override-vLLM%200.6.6-6E4AFF)](docker/compose.vllm.yaml)
[![Docker](https://img.shields.io/badge/packaging-Docker%20Compose-2496ED?logo=docker&logoColor=white)](docker/docker-compose.yml)
[![OpenTofu](https://img.shields.io/badge/IaC-OpenTofu%201.13-844FBA?logo=opentofu&logoColor=white)](terraform/)
![OCI](https://img.shields.io/badge/certified%20on-Oracle%20Cloud-F80000?logo=oracle&logoColor=white)
![Stage](https://img.shields.io/badge/stage-7%20%C2%B7%20audited%20%26%20documented-brightgreen)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

</div>

---

## Overview

Most "AI agent" demos are a thin wrapper around a hosted API: the model is remote,
the transcript is logged somewhere, and the orchestration is a prompt string. This
project takes the opposite position. It runs a **small, inspectable multi-agent
system entirely on hardware you control**, with the orchestration logic, the agent
contracts and the security boundary all readable in the repository.

A task arrives over an authenticated HTTP API. A **planner** decomposes it into
bounded subtasks. A **capability router** delegates each subtask to the specialist
that declares that capability — research, implementation or verification — and those
specialists run concurrently. A **critic** then synthesises the turns into a final
artifact, rejecting claims the transcript does not support. Every model call is
budgeted, every tool invocation is gated, and the response is a structured run
record that names the agent, duration and tools behind each turn.

Inference is served by **Ollama** (CPU, default) or **vLLM** (GPU override) behind
the same OpenAI-compatible `/v1` contract, so the backend is a configuration
decision rather than a code change. Everything is packaged with Docker Compose,
deployable on a workstation or on high-RAM ARM compute with a persistent block
volume, and provisioned on OCI with OpenTofu.

The project was built and verified in seven stages: scaffold → infrastructure
contract → operator docs → local evaluation → live OCI deployment → clean teardown →
security audit, documentation and cost model. Every stage's measured evidence is in
[`docs/`](docs/), including the parts that did not go to plan.

## ✨ Features

| Capability | What it means in practice |
| --- | --- |
| **Declared agent personas** | Planner, researcher, coder and critic are YAML: prompt template, capabilities, tool grants, token/temperature envelope. Adding an agent is a config change plus a prompt file — no orchestrator edit. |
| **Capability-based delegation** | A workflow stage either names a persona or declares a capability; the router matches it against the registry. Unknown capabilities fail the plan instead of silently running the wrong agent. |
| **Workflows as data** | `plan → fan-out → synthesis` is declared in `configs/workflows.yaml`, with per-stage mode (`single` / `fanout`), instruction template and task ceiling. Three workflows ship: `research_brief`, `code_review`, `reasoning_chain`. |
| **Hard budgets** | Step count, wall-clock and fan-out width are enforced in the loop. The effective budget is the **minimum** of the workflow, the task and the environment — a task can lower it, never raise it. |
| **Two-tool whitelist** | `calculator` (AST-parsed, numeric whitelist) and `text_stats`. No shell, no filesystem, no network. A persona may only call a tool it declares *and* the task allows. |
| **JSON-Schema task contract** | Tasks are validated against `configs/tasks/schema.json` before a single model token is spent. |
| **Backend-agnostic inference** | Ollama on CPU, vLLM on GPU, or any OpenAI-compatible server, selected by `SWARM_LLM_BASE_URL` + `SWARM_LLM_MODEL`. |
| **Hardened containers** | `cap_drop: ALL`, `no-new-privileges`, read-only orchestrator rootfs with a 64 MiB `/tmp`, pids limits, memory/CPU envelopes, 20 MiB × 3 log rotation, loopback-only publishing. |
| **Authenticated control plane** | `POST /v1/tasks` requires a bearer token compared in constant time; the worker refuses to start without one. `/health` and `/ready` are separate liveness and dependency probes. |
| **Persistent storage overlay** | `docker/compose.oci.yaml` bind-mounts weights and state from a block volume so a rebuilt container or a rebooted host keeps its model. |
| **Cloud-agnostic IaC** | OpenTofu modules for network, compute and storage with a provider-neutral variable/output contract; the AWS and GCP ports are documented down to the resource level. |
| **Evidence-first** | Every claim in this README traces to a measurement in `docs/`, including the failure modes and the honest limits. |

## 🏗 Architecture

The end-to-end path a request takes:

```text
[ User / Task Request ]
          │   POST /v1/tasks        Authorization: Bearer <orchestrator token>
          ▼
( Agent Orchestrator / Swarm )
   planner ─┬─ researcher ─┐
            ├─ coder       ├── capability router · tool gateway · step/time budgets
            └─ critic ─────┘
          │   POST /v1/chat/completions        (OpenAI-compatible, loopback)
          ▼
[ High-Spec OCI Compute & Persistent Block Storage ]
   VM.Standard.A2.Flex · 4 OCPU / 24 GB · 200 GB block volume at /mnt/llm-swarm
          │
          ▼
[ Local LLM Runtime (vLLM / Ollama) ]
   qwen2.5:3b-instruct · /v1/chat/completions · no outbound model traffic
          │
          ▼
( Collaborative Multi-Step Reasoning & Output )
   plan → concurrent specialist turns → critic synthesis → run record
```

A single workflow, expanded — `research_brief` at `max_tasks: 3`:

```text
task ──► planner (single)                1 model call
             │  JSON array of ≤3 subtasks
             ├──────────────┬──────────────┐
             ▼              ▼              ▼
         researcher     researcher     researcher        fan-out, concurrent
             │              │              │
             └──────────────┴──────────────┘
                            ▼
                       critic (single)                   1 model call
                            │  synthesis + unsupported-claim flags
                            ▼
                    run record (JSON)
```

Deployment topology, as provisioned in Stage 5 and destroyed in Stage 6:

```text
                       Internet
                          │   22 / 11434 / 8090 — operator /32 only, never 0.0.0.0/0
                          ▼
        ┌─────────────────────────────────────────────┐
        │ VCN 10.10.0.0/16                            │
        │  ├ internet gateway + public route table    │
        │  ├ security list (explicit ingress/egress)  │
        │  └ public subnet 10.10.1.0/24               │
        │         │                                   │
        │         ▼                                   │
        │  VM.Standard.A2.Flex  4 OCPU / 24 GB        │  aarch64, Ubuntu 24.04
        │  ├ cg-swarm-orchestrator  127.0.0.1:8090    │
        │  └ cg-swarm-llm           127.0.0.1:11434   │
        │         │ paravirtualized attachment        │
        │         ▼                                   │
        │  Block volume 200 GB, ext4 label llmswarm   │
        │  /mnt/llm-swarm/{models,state}              │
        └─────────────────────────────────────────────┘
```

Operators reach both services through an SSH tunnel (`ssh -N -L 8090:127.0.0.1:8090
-L 11434:127.0.0.1:11434`); the ports are bound to loopback in Compose and the
security list is the outer gate.

### One run, step by step

1. The task is parsed, normalised and validated against the JSON Schema and the
   loaded workflow set. An unknown workflow or a malformed constraint is rejected
   before any inference.
2. The **planner** returns a JSON array of subtasks, capped by `workflow.max_tasks`.
   Unparseable plans degrade to `completed_with_warnings` rather than crashing.
3. Each later stage runs once (`single`) or once per subtask (`fanout`), with the
   transcript of prior turns appended to that stage's instruction.
4. A persona may request a tool inside a fenced JSON block; the orchestrator executes
   it only if the persona grants the tool **and** the task allows it, and records the
   observation either way.
5. The run ends as `completed`, `completed_with_warnings`, `budget_exhausted`,
   `timeout` or `failed` — always with the reason, never silently truncated.

## 🧩 Components

| Service | Image | Responsibility | Default bind |
| --- | --- | --- | --- |
| `llm-runtime` | `ollama/ollama:0.5.7` | Local inference behind the OpenAI-compatible `/v1` | `127.0.0.1:11434` |
| `swarm-orchestrator` | `cloudguard/llm-swarm:local` | Persona registry, delegation, tool gateway, task API | `127.0.0.1:8090` |

| Layer | Choice | Why |
| --- | --- | --- |
| Orchestration | Python 3.12, stdlib HTTP | No agent-framework lock-in; the loop, budgets and gateway fit in a handful of readable modules |
| Personas | `configs/agents.yaml` + `configs/prompts/*.md` | Agent behaviour is configuration, reviewable in a diff |
| Workflows | `configs/workflows.yaml` | Topology is data; a workflow change cannot corrupt the loop |
| Tools | `calculator`, `text_stats` | Deterministic and side-effect-free, so a model cannot escalate through them |
| Task contract | JSON Schema 2020-12 | Invalid work is rejected before it costs tokens |
| Packaging | Docker Compose | Same two services on a laptop and on cloud compute |
| Infrastructure | OpenTofu | VCN, security list, ARM compute, 200 GB block volume, cloud-init |

## 🚀 Quickstart

### Docker (recommended)

```bash
cp .env.example .env                              # placeholders only; .env is gitignored
# set a real token in .env:
#   SWARM_ORCHESTRATOR_TOKEN=$(python -c "import secrets; print(secrets.token_hex(32))")

docker compose --env-file .env -f docker/docker-compose.yml config -q   # resolve
docker compose --env-file .env -f docker/docker-compose.yml up -d --build

# weights land in the model-weights volume, never in the checkout
docker compose --env-file .env -f docker/docker-compose.yml \
  exec llm-runtime ollama pull qwen2.5:3b-instruct

curl -s http://127.0.0.1:8090/health
curl -s http://127.0.0.1:8090/ready
```

Run a workflow:

```bash
TOKEN="$(grep '^SWARM_ORCHESTRATOR_TOKEN=' .env | cut -d= -f2-)"

curl -s http://127.0.0.1:8090/v1/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  --data @configs/tasks/research-brief.json
```

### Without Docker

The configuration and the loop are testable on a bare interpreter; `--dry-run`
validates a workflow and renders the delegation plan without calling a model:

```bash
python -m unittest discover -s tests -v                      # 33 tests
PYTHONPATH=src python -m swarm --list                        # personas, workflows, tools
PYTHONPATH=src python -m swarm --task configs/tasks/code-review.json --dry-run
PYTHONPATH=src python -m swarm --objective "Compare two queue designs" --workflow reasoning_chain
```

CLI exit codes: `0` success, `2` configuration/usage error, `3` the run itself failed.

## 🎭 Configuring Agent Personas

A persona is a declared role — not a prompt string buried in code:

```yaml
# configs/agents.yaml
personas:
  - id: researcher
    name: Researcher
    prompt: prompts/researcher.md        # template rendered per turn
    capabilities: [research, synthesis, summarisation]
    tools: [text_stats]                  # the ONLY tools this persona may call
    max_tokens: 900
    temperature: 0.3
```

| Persona | Capabilities | Tools | Temperature | Role in a run |
| --- | --- | --- | --- | --- |
| `planner` | planning, decomposition, routing | — | 0.1 | Decomposes the objective into a bounded JSON plan |
| `researcher` | research, synthesis, summarisation | `text_stats` | 0.3 | Executes research subtasks concurrently |
| `coder` | code, implementation, refactor, review | `calculator` | 0.1 | Reviews or drafts against a named angle |
| `critic` | review, verification, risk | `calculator`, `text_stats` | 0.1 | Aggregates, rejects unsupported claims, verdicts |

**Add a persona in four steps** — (1) create `configs/prompts/<id>.md`, (2) add an
entry to `agents.yaml` with its capabilities and tool grants, (3) reference it from a
workflow stage by `persona: <id>` or let the capability router match it, (4) restart
the orchestrator (`docker compose ... up -d`) — the registry is validated at start-up
and the process refuses to serve a malformed registry.

Rules the registry enforces:

- `SWARM_MAX_AGENTS` (1–16) caps the registry size; a larger file is a start-up error.
- A persona listed in a workflow but absent from the registry fails validation.
- Tool grants are a union between persona and task: `allowed_tools` in the task can
  only narrow what a persona may call.
- Temperature and `max_tokens` are per-persona defaults, overridable per call.

## 🔀 Multi-Step Collaborative Workflows

```yaml
# configs/workflows.yaml (abridged)
research_brief:
  entry: planner
  max_steps: 8
  max_tasks: 3
  stages:
    - { id: plan,       persona: planner,    mode: single }
    - { id: gather,     persona: researcher, mode: fanout }
    - { id: synthesise, persona: critic,     mode: single }
```

| Workflow | Shape | `max_steps` / `max_tasks` | Measured on the OCI host |
| --- | --- | --- | --- |
| `research_brief` | plan → research fan-out → cited brief | 8 / 3 | completed, 249 s |
| `code_review` | plan → review fan-out → blocking-findings verdict | 8 / 3 | completed, 208 s |
| `reasoning_chain` | plan → reasoning fan-out → chain verification | 10 / 4 | completed, 310 s (after raising the task timeout) |

**Stage modes.** `single` runs the stage once. `fanout` runs it once per planned
subtask, concurrently within the run loop, with the prior transcript appended.

**Template placeholders** available to every instruction: `{objective}`,
`{subtask}`, `{plan_text}`, `{max_tasks}`.

**Budget arithmetic.** The effective step budget is
`min(workflow.max_steps, task.constraints.max_steps, SWARM_MAX_STEPS)`; the wall-clock
ceiling is `SWARM_TASK_TIMEOUT_SECONDS`. Exhaustion is reported as a status, not an
exception: a run that hits its ceiling returns `budget_exhausted` with the turns it
did produce.

Example task:

```json
{
  "id": "research-local-llm-serving",
  "objective": "Compare Ollama and vLLM for serving a 3B model on a CPU-only node.",
  "workflow": "research_brief",
  "inputs": { "max_words": 400 },
  "constraints": { "max_steps": 8, "allowed_tools": ["text_stats"] }
}
```

## 🔌 Managing Model Backends

| Backend | When | How to select |
| --- | --- | --- |
| **Ollama** (default) | CPU-only hosts, laptops, the tested configuration | `docker/docker-compose.yml` + `ollama pull <tag>` |
| **vLLM** | GPU hosts needing higher throughput | add `-f docker/compose.vllm.yaml`; weights in the `model-weights` volume |
| **Any OpenAI-compatible server** | Existing inference gateway | set `SWARM_LLM_BASE_URL`, `SWARM_LLM_MODEL`, optional `SWARM_LLM_API_KEY` |

```bash
# GPU backend
docker compose --env-file .env \
  -f docker/docker-compose.yml -f docker/compose.vllm.yaml up -d --build
# keep SWARM_LLM_MODEL equal to --served-model-name so agent requests resolve
```

Optional pinned model profile — sampling parameters and the shared system preamble,
never applied automatically:

```bash
docker compose --env-file .env -f docker/docker-compose.yml \
  exec llm-runtime ollama create swarm-agent -f /config/Modelfile.swarm
# then SWARM_LLM_MODEL=swarm-agent
```

Sizing guidance (measured, not guessed — the 3B figure is from Stages 4–5):

| Model class | Weights on disk | Resident RAM at 4k context | Fits 4 OCPU / 24 GB? |
| --- | --- | --- | --- |
| 0.5B | ~0.4 GB | ~1 GB | Yes, ample headroom |
| 3B (tested) | ~1.9 GB | ~2.2–4 GB | Yes — ~7 tok/s warm on 8 vCPU |
| 8B (Q4) | ~4.7 GB | ~7–9 GB | Tight; measure before claiming |
| 13B+ | 8 GB+ | 14 GB+ | No, not with Docker overhead at 4k |

On a cloud host, keep weights on the block volume: the OCI overlay mounts
`/mnt/llm-swarm/{models,state}` into the containers and pins them with an ext4 label
and a `nofail` fstab entry, so a reboot remounts before Compose starts.

## ⚙️ Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `SWARM_LLM_BASE_URL` | `http://127.0.0.1:11434` | Inference server; `/v1` appended when absent |
| `SWARM_LLM_MODEL` | `qwen2.5:3b-instruct` | Model tag sent with every completion |
| `SWARM_LLM_API_KEY` | empty | Optional bearer key for a proxied endpoint |
| `SWARM_LLM_TIMEOUT_SECONDS` | `120` | Per-completion timeout |
| `SWARM_ORCHESTRATOR_TOKEN` | — | **Required**; the task API refuses to start without it |
| `SWARM_MAX_AGENTS` | `4` | Registry ceiling (validated 1–16) |
| `SWARM_MAX_STEPS` | `12` | Global model-call budget (validated 1–64) |
| `SWARM_TASK_TIMEOUT_SECONDS` | `300` | Wall-clock ceiling for one run; 900 was needed for CPU inference |
| `SWARM_TEMPERATURE` | `0.2` | Default sampling temperature |
| `SWARM_STATE_DIR` | `/app/state` | **Container** path for run state — not a host path |
| `SWARM_HOST_MODEL_DIR` / `SWARM_HOST_STATE_DIR` | `/mnt/llm-swarm/{models,state}` | Host paths bound in by the OCI overlay only |
| `OLLAMA_MEM_LIMIT` / `OLLAMA_CPUS` | `12g` / `6` | Inference container envelope |
| `WORKER_MEM_LIMIT` / `WORKER_CPUS` | `2g` / `2` | Orchestrator envelope |

Secrets are read from the environment only. The process never opens `.env`, never
logs the token, and answers `401` for a missing or mismatched bearer token.

## 🔐 Security Posture

- **Zero-leak Git boundary.** `.env*`, keys, PEMs, model weights, agent state,
  databases, logs, Terraform state and tfvars are ignored; only templates are
  tracked. Audited in Stage 7: 63 tracked files, 437 KB total, largest file 24 KB,
  **zero** sensitive artefacts in the index — see
  [`docs/stage-7-security-audit.txt`](docs/stage-7-security-audit.txt).
- **No tool escape.** The calculator parses with `ast` and evaluates a numeric
  whitelist; there is no `eval`, no subprocess, no filesystem read, no network call.
  Unknown tools, undeclared arguments and non-granted personas are refused and
  recorded as failed observations.
- **Bounded work.** Step, wall-clock and fan-out ceilings are enforced in the loop;
  a runaway plan cannot spawn unbounded model calls.
- **Least-privilege containers.** `cap_drop: ALL`, `no-new-privileges`, read-only
  orchestrator rootfs, pids limits, log rotation, loopback publishing only.
- **Network perimeter.** Ingress limited to 22 and the two application ports from a
  single operator `/32`; `0.0.0.0/0` is rejected at plan time. Egress is explicit:
  80/443 for packages and weights, 53 for DNS.
- **No weights in Git or in the image.** Model blobs live in a Docker volume or on
  the block volume; the Dockerfile bakes none.
- **No credentials in metadata.** cloud-init carries no token and no `.env`; the
  host's `.env` is created at deploy time, mode 600, outside Git.

## ☁️ Cloud Portability & Certification Scope

The Compose contract and the OpenTofu variable/output contract are provider neutral.
Only `terraform/provider.tf` and the three module bodies name a cloud's resources.
The OCI implementation is the one that was built, applied, run and destroyed.

> **Certification statement.** While the infrastructure-as-code is cloud-agnostic and
> supports deployment on AWS, GCP and OCI, **final production verification and testing
> certification for this project were performed strictly on Oracle Cloud
> Infrastructure (OCI)** — using trial credits with high-RAM **Ampere ARM** instances
> (`VM.Standard.A2.Flex`, 4 OCPU / 24 GB) and a persistent **OCI Block Volume**
> (200 GB, ext4, mounted `/mnt/llm-swarm`). AWS and GCP are supported ports, **not
> certified targets**: no AWS or GCP deployment, benchmark or certification run is
> claimed anywhere in this repository.

### Migration tips — AWS (EC2 + EBS)

| Contract | OCI (implemented) | AWS |
| --- | --- | --- |
| Network | VCN + IGW + route table + security list + subnet | VPC + IGW + route table + **security group** + subnet |
| Compute | `oci_core_instance` `VM.Standard.A2.Flex` | `aws_instance` `c6g.xlarge` (Graviton, arm64) or `t3.xlarge` (x86) |
| Image | Ubuntu 24.04 aarch64 resolved by shape | **an arm64 AMI is required**; an x86 AMI under `c6g` fails at launch |
| Storage | `oci_core_volume` + paravirtualized attachment | `aws_ebs_volume` (gp3) + `aws_volume_attachment` |
| Bootstrap | `metadata.user_data` (base64) | `user_data` (plain text) |
| Perimeter | security list on the subnet | security group on the instance ENI |
| State | OCI Object Storage S3-compatible backend | S3 backend + DynamoDB lock table |

Practical notes: the data volume appears as `/dev/nvme1n1` (Nitro) rather than
`/dev/sdb`, so mount by **filesystem label** as this project already does; attach the
EBS volume in the same AZ as the instance; `gp3` includes 3,000 IOPS and 125 MB/s, so
raise them only if you measure a need; arm64 `c6g` needs arm64 container images — the
worker image is multi-arch by design, but verify with `docker image inspect`.

### Migration tips — GCP (GCE + Persistent Disk)

| Contract | OCI (implemented) | GCP |
| --- | --- | --- |
| Network | VCN + IGW + route table + security list + subnet | VPC network + **firewall rules** + subnetwork (no IGW object) |
| Compute | `oci_core_instance` | `google_compute_instance` `t2a-standard-4` (Axion/ARM) or `e2-standard-4` (x86) |
| Image | resolved per shape architecture | `ubuntu-2404-lts-arm64` family for Tau T2A, `ubuntu-2404-lts` for E2 |
| Storage | `oci_core_volume` + attachment | `google_compute_disk` (pd-balanced) + `google_compute_attached_disk` |
| Bootstrap | `metadata.user_data` | `metadata_startup_script` |
| Perimeter | security list | VPC firewall rule with `source_ranges` (and a target tag) |
| State | OCI Object Storage | GCS backend |

Practical notes: GCP firewalls attach to the **network**, not the instance, so scope a
rule with `target_tags` instead of assuming per-instance isolation; attach the disk with
`device_name` and mount `/dev/disk/by-id/google-<device_name>` or by label; `pd-balanced`
is the closest match to OCI Balanced (60 IOPS/GB); an **external IP is opt-in**
(`access_config {}`) — omit it and reach the API through IAP or a bastion.

## 💰 Cross-Cloud Cost & Benchmark Estimation

Monthly list cost for the **tested footprint**: 8 vCPU, 24 GB RAM, one 200 GB
persistent data volume, 730 hours, Linux, on-demand, excluding VAT and egress. The
A2 row is what Stage 5 actually ran; the Always Free row is what Stage 5 tried first.

| Cloud | Shape | vCPU / RAM | Compute $/hr | Compute $/mo | 200 GB disk $/mo | **Total $/mo** |
| --- | --- | --- | --- | --- | --- | --- |
| **OCI** | `VM.Standard.A2.Flex` — **tested** | 8 / 24 GB | $0.1040 | $75.92 | $8.50 (Balanced) | **$84.42** |
| **OCI** | `VM.Standard.A1.Flex` — Always Free target | 8 / 24 GB | $0.00 | $0.00 | $0.00¹ | **$0.00**¹ |
| **AWS** | `t3.xlarge` (x86, burstable) | 4 / 16 GB | $0.1664 | $121.47 | $16.00 (gp3) | **$137.47** |
| **AWS** | `c6g.xlarge` (Graviton2) | 4 / 8 GB | $0.1360 | $99.28 | $16.00 (gp3) | **$115.28** |
| **GCP** | `e2-standard-4` (x86) | 4 / 16 GB | $0.1340 | $97.84 | $20.00 (pd-balanced) | **$117.84** |

¹ Always Free covers 1,500 OCPU-hours + 9,000 GB-hours/month of `A1.Flex` and 200 GB
of block storage *in total*, so 4 OCPU / 24 GB is free **when the shape can be
allocated**. In Stage 5 it could not: 45 attempts across three Frankfurt ADs returned
`500-InternalError, Out of host capacity`, which is why the run used A2 on trial credit.

**Rate derivation** (so the arithmetic is checkable, not asserted): OCI A2 =
$0.014/OCPU-hr + $0.002/GB-hr → `4×0.014 + 24×0.002 = $0.104/hr`; OCI Balanced block
= `$0.0255/GB-mo` capacity + `10 VPU × $0.0017` = `$0.0425/GB-mo` → 200 GB = $8.50.
AWS gp3 = `$0.08/GB-mo`; GCP `pd-balanced` = `$0.10/GB-mo`.

**RAM parity matters more than the headline price.** The tested deployment needs
24 GB. `c6g.xlarge` (8 GB) and `e2-standard-4` (16 GB) do not provide it, so a fair
like-for-like comparison has to step up: `c6g.2xlarge` (8 vCPU / 16 GB) is
≈ $198.56/mo + $16 disk, `r6g.xlarge` (4 vCPU / 32 GB) ≈ $147.17/mo + $16, and
`e2-standard-8` (8 vCPU / 32 GB) is $195.68/mo + $20. OCI's flexible 4 OCPU / 24 GB
sits at $84.42 with no overshoot — on this workload, the ARM flexible shape is the
cheapest way to get exactly the memory a 3B model needs.

### Measured benchmark (not a cross-cloud claim)

| Footprint | Model | Throughput | Task wall-clock | Source |
| --- | --- | --- | --- | --- |
| Workstation, 16 logical CPUs, CPU-only | `qwen2.5:3b-instruct` | median 13.9 tok/s (12.3–15.4) | 46–218 s per task; 218 s total at concurrency 2 | Stage 4 |
| OCI `A2.Flex`, 4 OCPU (8 vCPU), 24 GB | `qwen2.5:3b-instruct` | 6.7–7.8 tok/s warm (3.3–3.9 at the default 4-CPU cap) | 208 s, 249 s, 310 s for the three workflows | Stage 5 |
| AWS / GCP | — | **not measured** | **not measured** | port documented only |

Derived unit economics on the tested OCI footprint: 7 tok/s × 3,600 s × 730 h ≈
**18.4 M tokens/month** for $84.42, i.e. **≈ $4.59 per million output tokens** at
100% duty on a single stream. That is arithmetic on the measured rate, not a
benchmark of a different shape.

**Egress and network.** All three clouds make inbound transfer (image and model pulls)
free. Outbound allowance differs: OCI includes 10 TB/month, AWS 100 GB/month (then
≈ $0.09/GB), GCP 200 GB/month on Premium tier. The swarm's steady-state egress is
negligible — the API returns JSON to an operator over an SSH tunnel — so egress is
listed for completeness rather than counted in the totals. Bandwidth ceilings also
differ: A2 gives 1 Gbps per OCPU, `t3` up to 5 Gbps, `c6g` up to 10 Gbps, `e2` up to
8 Gbps; none of it was the constraint here — **CPU was**.

**What these numbers are not.** They are list prices checked on 2026-11-18 from
published price pages, not invoices. They exclude VAT, support plans, snapshots,
backup storage and committed-use discounts (which reach −33% on OCI, −27% on AWS
Compute Savings Plans and −37% on GCP 1-year CUD). Stage 6 could not read a billing
API total, so no cloud bill is claimed anywhere in this repository.

## 📊 Verified Results

| Stage | What was measured | Evidence |
| --- | --- | --- |
| 1 | 33 unit tests, compose config for canonical/root/GPU entrypoints, 182 MB image as uid 10001, live run on `qwen2.5:0.5b` (5 calls, 12.7 s) and a budget-exhaustion run | [docs/stage-1](docs/stage-1-verification.txt) |
| 2 | `fmt`, `validate`, plan of 8 resources | [docs/stage-2](docs/stage-2-verification.txt) |
| 3 | Operator checklist + agent playbook, links validated | [docs/stage-3](docs/stage-3-verification.txt) |
| 4 | 3/3 workflows completed locally; 46–218 s; 13.9 tok/s median; 4.1 GiB peak; 0 secret hits in 416 log lines | [docs/stage-4](docs/stage-4-verification.txt) |
| 5 | Live OCI host: 3/3 workflows, 6.7–7.8 tok/s warm, 853% CPU peak, no OOM, volume-backed weights, reboot persistence | [docs/stage-5](docs/stage-5-verification.txt) |
| 6 | Both stacks stopped, 8/8 resources destroyed, empty state, provider inventory 0 live | [docs/stage-6](docs/stage-6-verification.txt) |
| 7 | Zero-leak audit signed off, README rewrite, cross-cloud cost model | [docs/stage-7](docs/stage-7-security-audit.txt) |

## ⚠️ Honest Limits

- **There is no live deployment right now.** Stage 6 destroyed the OCI host and its
  volume; the Stage 5 numbers are historical measurements of a deployment that
  existed, not a running endpoint.
- **The certified run used A2, not Always Free A1.** `A1.Flex` returned
  `500-InternalError, Out of host capacity` in all three Frankfurt ADs across 45
  attempts, and the tenancy is subscribed to Frankfurt only. The Always Free claim is
  therefore **untested**, and A2 was credit-funded.
- **Performance is one model, one node, one sample each.** The unit suite runs against
  a scripted model double; no claim is made about larger models, GPU throughput,
  concurrency beyond two runs, or run-to-run variance.
- **The code-review workflow cannot read source files.** The tool whitelist is
  `calculator`/`text_stats`, so the coder reviews from the objective text; findings are
  generic and review recall is not claimed.
- **Run records are returned, not persisted.** They live in the HTTP response and
  container logs; the state bind mount is provisioned but the worker does not yet
  write run records to it.
- **Single node, single process.** Fan-out is concurrent *within* one run loop;
  distributing agents across nodes is future work (Stage 8).
- **The GPU override is unexecuted.** `docker/compose.vllm.yaml` is validated only;
  no GPU host was available, and vLLM was never driven by this orchestrator.
- **No authentication beyond a shared bearer token.** No per-user identity, no audit
  store, no TLS: the control plane is loopback-only by design, reached through SSH.
- **Costs are list prices, not bills.** See the cost section; no billing API figure
  exists in this repository.
- **AWS and GCP are documentation, not deployments.** Their migration tables are
  reasoned porting guidance from the provider-neutral contract, not executed runs.

## 🗺️ Stage Roadmap

| Stage | Deliverable | Exit criteria |
| --- | --- | --- |
| 1 | Scaffold, Compose topology, personas, workflows, task schema, orchestration loop | tests, compose config, image build, local API surface — **done** |
| 2 | OCI network, inference node, block volume, cloud-init | `fmt`, `validate`, plan of 8 — **done** |
| 3 | Agent guide and manual prerequisites | both documents complete, links valid — **done** |
| 4 | Local evaluation: task success, latency, memory | 3/3 workflows, measured latency/throughput/CPU/RAM, log audit — **done** |
| 5 | Live OCI deployment | cloud-init done, volume mounted, 3/3 workflows on the host — **done** |
| 6 | Clean teardown and zero-resource verification | 8/8 destroyed, empty state, 0 live resources — **done** |
| 7 | Security audit, README documentation, cost model | zero-leak audit signed off, cost table published — **done** |
| 8 | Scale and recovery | load test, multi-node fan-out, restart/recovery drill |

## 📚 Documentation

| Document | Purpose |
| --- | --- |
| [`MANUAL_PREREQS.md`](MANUAL_PREREQS.md) | Human operator checklist: OCI credentials, SSH key, block volume, models, `.env`, teardown |
| [`AGENT_GUIDE.md`](AGENT_GUIDE.md) | AI-agent playbook: bring-up, monitoring, delegation debugging, failure table |
| [`terraform/README.md`](terraform/README.md) | Module map, inputs/outputs, security posture, portability table |
| [`docs/stage-7-security-audit.txt`](docs/stage-7-security-audit.txt) | Zero-leak audit, findings and sign-off |
| [`docs/`](docs/) | Per-stage verification records, including failures and deviations |

## License

MIT — see [LICENSE](LICENSE).
