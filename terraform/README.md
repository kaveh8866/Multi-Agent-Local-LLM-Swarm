# OpenTofu — OCI infrastructure for the multi-agent swarm

Status: **validated and planned, not applied.** `tofu fmt -check` and
`tofu validate` pass, and a read-only plan reports 8 resources to add. No
resource exists in OCI, and therefore no state file exists. Evidence:
[`../docs/stage-2-verification.txt`](../docs/stage-2-verification.txt).

## What it provisions

```
                     Internet
                        │  22/tcp        from ssh_allowed_cidrs
                        │  11434, 8090   from llm_api_cidrs
                        ▼
        ┌──────────────────────────────────────┐
        │ VCN 10.10.0.0/16                     │
        │  ├ internet gateway + public route   │
        │  ├ security list (explicit egress)   │
        │  └ public subnet 10.10.1.0/24        │
        │        │                             │
        │        ▼                             │
        │  VM.Standard.A1.Flex  4 OCPU / 24 GB │
        │  Canonical Ubuntu 24.04 (aarch64)    │
        │        │ paravirtualized attachment  │
        │        ▼                             │
        │  Block volume 200 GB (Balanced)      │
        │  /mnt/llm-swarm  ext4 label llmswarm │
        └──────────────────────────────────────┘
```

| Module | Resources | Purpose |
| --- | --- | --- |
| `modules/network` | VCN, internet gateway, route table, security list, public subnet | Network perimeter with an explicit port/source inventory |
| `modules/compute` | Availability-domain lookup, Ubuntu image lookup, `oci_core_instance`, VNIC reads | High-memory Ampere instance with cloud-init metadata |
| `modules/storage` | `oci_core_volume`, `oci_core_volume_attachment` | Persistent weights, caches and agent state |

The root module (`main.tf`) only wires variables and modules; every
provider-specific decision lives in a module body.

## Usage

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars     # gitignored; fill real OCIDs
tofu init
tofu fmt -recursive -check
tofu validate
tofu plan -out=stage2.tfplan
tofu apply stage2.tfplan                         # creates billable resources
```

Outputs include `instance_public_ip`, `block_volume_id`,
`block_volume_attachment_id`, `block_volume_device`, `block_volume_mount_point`,
`ingress_exposure` and `ssh_connection`.

## Key inputs

| Variable | Default | Notes |
| --- | --- | --- |
| `tenancy_ocid`, `compartment_ocid`, `region` | — / — / `eu-frankfurt-1` | OCIDs are validated by regex |
| `ssh_allowed_cidrs` | `203.0.113.10/32` | Must not be `0.0.0.0/0` (plan-time rejection) |
| `llm_api_cidrs` | `203.0.113.10/32` | Sources for the inference and task-API ports |
| `llm_ports` | `[11434, 8090]` | Ollama `/v1` and the swarm task API; vLLM native is 8000 |
| `instance_shape` | `VM.Standard.A1.Flex` | Ampere ARM; `VM.Standard3.Flex` is the x86 alternative |
| `instance_ocpus` / `instance_memory_gbs` | `4` / `24` | Always Free A1 ceiling; raise both for an 8B model |
| `block_volume_size_gbs` | `200` | Weights + caches + run records |
| `block_volume_vpus_per_gb` | `10` | Balanced; 0 is Lower Cost |
| `volume_mount_point` | `/mnt/llm-swarm` | Created and mounted by cloud-init |

## Security posture

- SSH and the inference/task-API ports are admitted only from explicit CIDRs;
  both `0.0.0.0/0` and `::/0` are rejected by variable validation.
- No port 80/443 and no ICMP inbound. Egress is explicit: 80/443 for packages,
  images and model weights, 53 for DNS.
- No credential is ever placed in instance metadata or cloud-init. `.env` is
  created on the host at deployment time; model weights are downloaded by the
  host, not baked into an image.
- The inference API has no authentication in Ollama's default configuration and
  the task API runs over plain HTTP with a bearer token, so both stay on
  loopback in Compose and are further gated by the security list. The swarm is
  not an internet service.

## Storage and data safety

The block volume is independent of the instance lifecycle: replacing or
destroying the instance does not delete it, and cloud-init re-mounts it by ext4
label (`llmswarm`) through a `nofail` fstab entry and a systemd unit that
retries on every boot.

`tofu destroy` **does** delete the volume. Before a real model lands, add:

```hcl
lifecycle { prevent_destroy = true }
```

to `oci_core_volume.llm` and remove it deliberately for teardown.

The volume layout prepared by cloud-init is `models/` (root, Ollama weights),
`cache/` (root, inference cache) and `state/` (uid 10001, agent run records).
Stage 5 adds the Compose override that bind-mounts those paths instead of named
volumes; that wiring is not verified yet.

## Cost note

`VM.Standard.A1.Flex` at 4 OCPU / 24 GB is the Always Free allowance, and the
first 200 GB of block storage is free **in total**. A 100 GB boot volume plus a
200 GB data volume therefore consumes trial credit for the difference. Raise
`instance_ocpus` / `instance_memory_gbs` only against the credit allocation.

## Portability

The variable/output contract is provider neutral. Porting means replacing
`provider.tf` and the three module bodies; `variables.tf`, `outputs.tf` and
`main.tf` wiring stay intact.

| Contract | OCI (implemented) | AWS | GCP |
| --- | --- | --- | --- |
| Network | VCN, IGW, route table, security list, subnet | VPC, IGW, route table, security group, subnet | VPC network, firewall rule, subnetwork |
| Compute | `oci_core_instance` A1.Flex | `aws_instance` t4g.xlarge | `google_compute_instance` t2a-standard-4 |
| Storage | `oci_core_volume` + attachment | `aws_ebs_volume` (gp3) + attachment | `google_compute_disk` (pd-balanced) + attached disk |
| Bootstrap | `metadata.user_data` (base64) | `user_data` | `metadata_startup_script` |
| State | OCI Object Storage S3-compatible backend | S3 + DynamoDB lock | GCS |

Only the OCI implementation is validated. AWS and GCP are documented ports, not
tested targets.
