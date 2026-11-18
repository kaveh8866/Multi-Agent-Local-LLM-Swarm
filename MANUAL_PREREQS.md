# Manual Prerequisites — Multi-Agent Local LLM Swarm

Human operator checklist to complete **before** running any deployment command.
The AI-agent playbook is [`AGENT_GUIDE.md`](AGENT_GUIDE.md); this file is the
human side: credentials, keys, storage, model weights and `.env`.

Nothing in this checklist creates a billable resource by itself except
`tofu apply` (§7), which is called out explicitly. Every value you produce here
is local: `.env`, `terraform.tfvars` and `*.pem` are gitignored.

> **Certification scope.** The IaC and container configuration are
> cloud-agnostic (AWS, GCP or OCI). Final production verification and testing
> certification for this project are performed strictly on **Oracle Cloud
> Infrastructure (OCI)**, using trial credits with high-RAM **Ampere ARM**
> instances and persistent **OCI Block Volume** storage. AWS and GCP are
> supported ports, not certified targets.

## Checklist at a glance

- [ ] Workstation tools installed and verified (§1)
- [ ] OCI API key created, `~/.oci/config` written, mode 600 (§2)
- [ ] Tenancy, compartment and region OCIDs recorded outside Git (§2)
- [ ] SSH key pair generated; public key pasted into tfvars (§3)
- [ ] Operator public IP known; CIDRs set — never `0.0.0.0/0` (§4)
- [ ] `terraform.tfvars` complete; `tofu validate` and `tofu plan` reviewed (§5–6)
- [ ] Block volume mounted at `/mnt/llm-swarm` and verified on the host (§7)
- [ ] Model weights pulled into the weights volume; licence noted (§8)
- [ ] `.env` populated with a real orchestrator token; mode 600 (§9)
- [ ] Pre-deployment gate passed (§10)

---

## 0. Capacity planning first

Sizing decides the shape and the model, so do this before provisioning.

| Model class | Weights on disk | RAM at 4k context (approx) | Fits 24 GB A1? |
| --- | --- | --- | --- |
| 0.5B (e.g. `qwen2.5:0.5b`) | ~0.4 GB | ~1 GB | Yes, lots of headroom |
| 3B (e.g. `qwen2.5:3b-instruct`) | ~1.9 GB | ~3 GB | Yes, recommended default |
| 8B (e.g. `llama3.1:8b-instruct-q4_K_M`) | ~4.7 GB | ~7–9 GB | Tight at 4k; measure it |
| 13B+ | 8 GB+ | 14 GB+ | No at 4k on 24 GB with Docker overhead |

- Boot volume 100 GB holds Ubuntu, Docker and images.
- Data volume 200 GB holds weights, caches and run records; several 3B models
  fit comfortably.
- OCI Always Free covers 200 GB of block storage **in total** and the A1.Flex
  4 OCPU / 24 GB shape. A 100 GB boot + 200 GB data volume therefore consumes
  trial credit for the difference.

- [ ] I know which model I will run and it fits the shape in `terraform.tfvars`.

> **Capacity reality (measured in Stage 5).** `A1.Flex` returned
> `500-InternalError, Out of host capacity` for 4/24, 2/12 and 1/8 across all
> three Frankfurt ADs in 45 attempts over ~25 minutes, and the tenancy was
> subscribed to Frankfurt only. `A2.Flex` (also Ampere, credit-funded) placed on
> the first attempt. Decide before `tofu apply` whether you will wait for
> Always Free A1 capacity or accept A2 on trial credit.

## 1. Workstation tools

| Tool | Minimum | Used for |
| --- | --- | --- |
| Git | 2.30 | clone, commit, `check-ignore` boundary checks |
| Docker Engine / Desktop | 24 | local stack (`linux/amd64` or `linux/arm64`) |
| Docker Compose | v2 | topology |
| Python | 3.11 | unit tests and the `swarm` CLI |
| OpenSSH client | any | key generation and host access |
| OpenTofu | 1.6 | infrastructure (not Terraform 1.x syntax differences) |

```bash
git --version
docker version --format '{{.Server.Version}}'
docker compose version
python --version
ssh -V
tofu version
```

On this workspace OpenTofu ships at `.cloudguard/tools/tofu/tofu.exe`; elsewhere
install the standalone binary from opentofu.org and verify `tofu version` ≥ 1.6.

- [ ] All six commands return a version.

## 2. OCI credentials

### 2.1 Gather the four identifiers

From the OCI Console (top-right profile → Tenancy):

| Value | Where | Notes |
| --- | --- | --- |
| Tenancy OCID | Tenancy details | `ocid1.tenancy.oc1..…` |
| Compartment OCID | Identity → Compartments | use the project's own compartment |
| Region | Region selector | e.g. `eu-frankfurt-1` |
| User OCID | Identity → Users → your user | only needed for inline auth |

### 2.2 Create an API signing key

1. Identity → Users → your user → **API keys** → **Add API key**.
2. Choose **Generate API key pair**, download the private key.
3. Move it out of Downloads into `~/.oci/` and restrict it:

```bash
mkdir -p ~/.oci && chmod 700 ~/.oci
mv ~/Downloads/oci_api_key.pem ~/.oci/oci_api_key.pem
chmod 600 ~/.oci/oci_api_key.pem
```

4. Paste the configuration preview from the console into `~/.oci/config`:

```ini
[DEFAULT]
user=ocid1.user.oc1..replace-me
fingerprint=aa:bb:cc:dd:replace:me
tenancy=ocid1.tenancy.oc1..replace-me
region=eu-frankfurt-1
key_file=~/.oci/oci_api_key.pem
```

```bash
chmod 600 ~/.oci/config
```

The OCI CLI can do the same interactively: `oci setup config`.

### 2.3 Authorise the operations

The user needs permission in the target compartment to manage VCNs, instances
and block volumes. A minimal policy scoped to the compartment:

```text
Allow group <your-group> to manage virtual-network-family in compartment <name>
Allow group <your-group> to manage instance-family        in compartment <name>
Allow group <your-group> to manage volume-family          in compartment <name>
Allow group <your-group> to use   instances               in compartment <name>
```

Adjust to your tenancy's existing policy structure; do not widen to
`tenancy` scope for a single project.

- [ ] `~/.oci/config` exists, mode 600, `fingerprint` matches the key in the console.
- [ ] The API key is **not** inside this repository (`git check-ignore` proves it).
- [ ] Tenancy/compartment OCIDs are recorded somewhere private (password manager).

## 3. SSH key pair

Never reuse a personal key, and never paste a private key anywhere.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/llm-swarm -C "llm-swarm" -N ""
cat ~/.ssh/llm-swarm.pub        # paste this CONTENT into terraform.tfvars
```

```bash
chmod 600 ~/.ssh/llm-swarm      # Linux/macOS
ssh-add ~/.ssh/llm-swarm        # optional agent
```

- [ ] `~/.ssh/llm-swarm.pub` starts with `ssh-ed25519`.
- [ ] The private key `~/.ssh/llm-swarm` is outside the repository and mode 600.

## 4. Network inputs

```bash
curl -s https://ifconfig.me      # your current public IP
```

Use `<ip>/32` for `ssh_allowed_cidrs` and `llm_api_cidrs`. Both variables reject
`0.0.0.0/0` and `::/0` at plan time — that is deliberate. If you work from
changing addresses, use a VPN range rather than opening the world.

Ports admitted: `22` (SSH), `11434` (Ollama `/v1`), `8090` (swarm task API). Add
`8000` only if you move the backend to vLLM's native port. The Compose file binds
both application ports to loopback; the security list is the second gate.

- [ ] Operator CIDR recorded.
- [ ] No `0.0.0.0/0` in `ssh_allowed_cidrs` or `llm_api_cidrs`.

## 5. terraform.tfvars

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
chmod 600 terraform.tfvars             # Linux/macOS
git check-ignore -v terraform.tfvars   # must print the *.tfvars rule
```

Fill in: `tenancy_ocid`, `compartment_ocid`, `region`, `ssh_public_key` (the
*content*, not a path), `ssh_allowed_cidrs`, `llm_api_cidrs`, and the shape/model
sizing. Keep `config_file_profile = "DEFAULT"` unless you use another profile.

- [ ] `tofu fmt -check` and `tofu validate` pass.
- [ ] `terraform.tfvars` is ignored by Git and mode 600.

## 6. Review the plan before creating anything

```bash
cd terraform
tofu init
tofu plan -out=stage2.tfplan
```

Expected: **8 resources to add** (VCN, internet gateway, route table, security
list, subnet, instance, volume, volume attachment), 0 to change, 0 to destroy.
If the count differs, read the diff before applying.

**Cost gate.** `tofu apply stage2.tfplan` creates billable OCI resources. Confirm
with the account owner first, and check the tenancy's service limits for
`VM.Standard.A1.Flex` in the chosen region and availability domain.

```bash
tofu apply stage2.tfplan
tofu output instance_public_ip
tofu output block_volume_mount_point
```

- [ ] Plan reviewed: 8 to add, 0 warnings.
- [ ] Billing/credits confirmed by the account owner.
- [ ] Public IP and volume outputs captured (public information only).

## 7. Verify the block volume on the host

cloud-init discovers the attachment, formats it **once** (ext4 label `llmswarm`),
adds a `nofail` fstab entry, starts `llm-swarm-mount.service` and creates
`models/`, `cache/`, `state/`.

```bash
ssh -i ~/.ssh/llm-swarm ubuntu@<instance_public_ip>
cloud-init status --wait
systemctl status llm-swarm-mount --no-pager
findmnt /mnt/llm-swarm
df -h /mnt/llm-swarm
ls -ld /mnt/llm-swarm /mnt/llm-swarm/models /mnt/llm-swarm/cache /mnt/llm-swarm/state
```

Expected ownership: `models/` and `cache/` root (the Ollama container writes
them), `state/` uid 10001 (the orchestrator user).

**Manual fallback** (console-created volume, or cloud-init reported no device).
These commands are **destructive on a non-empty disk** — confirm the device with
`lsblk` first and never format a disk that already has data:

```bash
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT
sudo mkfs.ext4 -L llmswarm /dev/oracleoci/oraclevdb      # ONLY on an empty volume
echo 'LABEL=llmswarm /mnt/llm-swarm ext4 defaults,nofail,noatime 0 2' | sudo tee -a /etc/fstab
sudo mkdir -p /mnt/llm-swarm && sudo mount -a
sudo mkdir -p /mnt/llm-swarm/{models,cache,state}
sudo chown 10001:10001 /mnt/llm-swarm/state
```

- [ ] `findmnt` shows the data volume at `/mnt/llm-swarm`.
- [ ] The mount unit is `active (exited)`.
- [ ] `df -h` reports the configured size (200 GB default).

> **Known gap.** The Compose file uses Docker named volumes today. The Stage 5
> override that bind-mounts `/mnt/llm-swarm/{models,cache,state}` does not exist
> yet, so on a cloud host the volume is mounted but the stack still writes to
> Docker volumes until then. Do not claim persistence-on-block-volume yet.

## 8. Model weights

Weights are large, licensed artefacts. Pull them **inside** the inference
container so they land in the `cloudguard-llm-swarm-weights` volume, never in the
checkout.

```bash
# on the host, from the repository root
docker compose --env-file .env -f docker/docker-compose.yml \
  exec llm-runtime ollama pull qwen2.5:3b-instruct

docker compose --env-file .env -f docker/docker-compose.yml \
  exec llm-runtime ollama list
```

`ollama pull` fetches over HTTPS from the Ollama registry and verifies layer
digests; a truncated or tampered download fails verification rather than loading.
Do not install weights from unofficial mirrors, torrents or chat attachments.

Optional pinned profile:

```bash
docker compose --env-file .env -f docker/docker-compose.yml \
  exec llm-runtime ollama create swarm-agent -f /config/Modelfile.swarm
# then SWARM_LLM_MODEL=swarm-agent in .env
```

Licences: Qwen2.5 is Apache-2.0; Llama-family models carry the Meta Llama licence;
Mistral models carry their own terms. Read the model card before commercial use —
the licence is your responsibility, not the stack's.

Air-gapped transfer (no registry access): copy the volume contents from a machine
that has them, or export/import the model directory, and verify with
`ollama show <model>` on the target. Never commit weights to Git.

Disk checks:

```bash
docker system df -v | grep -E 'cloudguard-llm-swarm-weights'
docker run --rm -v cloudguard-llm-swarm-weights:/models alpine du -sh /models
```

- [ ] Model pulled and listed by `ollama list`.
- [ ] Licence reviewed for the intended use.
- [ ] Weights are in the volume, not in the working tree (`git status` clean).

## 9. Populate `.env`

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # orchestrator token
chmod 600 .env             # Windows: icacls .env /inheritance:r /grant:r "%USERNAME%:R"
git check-ignore -v .env
```

| Variable | What to put |
| --- | --- |
| `SWARM_ORCHESTRATOR_TOKEN` | the 64-hex token you just generated (required) |
| `SWARM_LLM_BASE_URL` | `http://llm-runtime:11434` inside Compose; `http://127.0.0.1:11434` outside |
| `SWARM_LLM_MODEL` | the tag you pulled, e.g. `qwen2.5:3b-instruct` |
| `SWARM_LLM_API_KEY` | leave placeholder unless an authenticating proxy fronts Ollama |
| `SWARM_MAX_STEPS` / `SWARM_TASK_TIMEOUT_SECONDS` | keep defaults until you have measurements |
| `OLLAMA_MEM_LIMIT` / `OLLAMA_CPUS` | keep ≤ the node's RAM and CPU count |
| `WORKER_MEM_LIMIT` / `WORKER_CPUS` | 2g / 2 is enough for the orchestrator |
| `OLLAMA_PORT` / `SWARM_PORT` | host bindings; change only on a conflict |

The token is a shared bearer secret: anyone who has it can spend your model
cycles. Rotate it by editing `.env` and recreating the worker container.

- [ ] Token is a fresh random value, not the placeholder.
- [ ] `.env` is mode 600 (or ACL-restricted) and ignored by Git.
- [ ] `SWARM_LLM_MODEL` matches a tag from `ollama list`.

## 10. Pre-deployment gate

Run all of these; a failure means stop, not "try anyway".

```bash
git status --porcelain                                     # clean
python -m unittest discover -s tests -q                    # 33 tests
docker compose --env-file .env -f docker/docker-compose.yml config -q
cd terraform && tofu fmt -recursive -check && tofu validate && tofu plan -input=false | tail -3
cd .. && git check-ignore -v .env terraform/terraform.tfvars terraform/.terraform
```

- [ ] Tests pass.
- [ ] Compose resolves.
- [ ] `tofu` formats, validates and plans 8 resources.
- [ ] `.env`, tfvars and `.terraform/` are ignored.
- [ ] No secret appears in `git status`, `git diff` or the plan output.

## 11. First run and where evidence goes

```bash
docker compose --env-file .env -f docker/docker-compose.yml up -d --build
curl -s http://127.0.0.1:8090/health | python -m json.tool
curl -s http://127.0.0.1:8090/ready  | python -m json.tool

TOKEN="$(grep '^SWARM_ORCHESTRATOR_TOKEN=' .env | cut -d= -f2-)"
curl -s http://127.0.0.1:8090/v1/tasks \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  --data @configs/tasks/research-brief.json | python -m json.tool
```

Record the measured output (status, step count, per-turn `duration_ms`, model
tag, host shape) in `docs/stage-N-verification.txt`. Do not describe a run as
verified until its numbers are written there.

## 12. Cost and quota notes

- `VM.Standard.A1.Flex` 4 OCPU / 24 GB is the Always Free allowance; more OCPU or
  RAM is credit-funded.
- Block storage: the first 200 GB is free **in total** across boot and data
  volumes.
- A1 capacity is region- and AD-dependent. If `apply` reports out of host
  capacity, pin another AD in `availability_domain` and re-plan; do not silently
  change to a paid x86 shape.
- Model pulls are egress traffic; a few GB per model is normal.

## 13. Certification scope

The container configuration and the OpenTofu modules are cloud-agnostic: the
same topology maps to AWS (EC2 + EBS + VPC) and GCP (GCE + Persistent Disk + VPC)
by replacing `provider.tf` and the three module bodies.

**Final production verification and testing certification are performed strictly
on Oracle Cloud Infrastructure**, with trial credits, high-RAM Ampere ARM compute
and persistent block storage. No AWS or GCP certification run is claimed.

## 14. Teardown and recovery

```bash
# stop the stack, keep weights and run records
docker compose --env-file .env -f docker/docker-compose.yml down

# DESTROYS model weights and agent state volumes — ask first
# docker compose --env-file .env -f docker/docker-compose.yml down -v
```

Before a real model lands on the block volume, add
`lifecycle { prevent_destroy = true }` to `oci_core_volume.llm` so a
`tofu destroy` cannot delete the data. For a full teardown: stop the stack, back
up run records, then `tofu plan -destroy` → operator approval → `tofu destroy` →
`tofu state list` (empty).

**Executed for this project (Stage 6, 2026-11-18).** Both Compose stacks were
stopped, `tofu destroy` reported `8 destroyed`, the state is empty at serial 67,
and a read-only provider inventory returned zero live project-owned resources.
No volume backup was taken, so the pulled weights went with the volume; a future
teardown that should preserve them needs a volume backup first. Evidence:
[`docs/stage-6-verification.txt`](docs/stage-6-verification.txt).

## 15. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `401 Unauthorized` from OCI during `tofu plan` | wrong fingerprint/key path/profile | compare `~/.oci/config` with the console API-key entry |
| `NotAuthorizedOrNotFound` on a resource | missing compartment policy | add the policy in §2.3 |
| `Out of host capacity` on apply | A1 stock exhausted across the region, not just that AD | retry AD-1/2/3 once each, then switch to `VM.Standard.A2.Flex` (credit-funded, same ARM profile) — A1 may stay full for days |
| `no data block device found` in cloud-init | volume attached after first pass | `systemctl restart llm-swarm-mount`; check `lsblk` |
| `Permission denied` writing `/mnt/llm-swarm` | volume not mounted (boot disk path) | `findmnt`, then re-run the mount unit |
| `pull access denied` / model 404 | wrong tag | `ollama list` locally, use a tag that exists |
| `port is already allocated` | native Ollama on 11434 | set `OLLAMA_PORT=11435` in `.env` |
| `/ready` 503 | model not loaded yet | wait, `ollama ps`, then re-probe |
| `SWARM_ORCHESTRATOR_TOKEN is not set` | `.env` not created or container started without it | `cp .env.example .env`, set token, recreate |
