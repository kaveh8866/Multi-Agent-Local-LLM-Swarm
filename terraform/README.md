# =============================================================================
# OpenTofu — Stage 2 contract (no resources defined yet)
# =============================================================================
# This directory is reserved for the OCI multi-node / high-spec compute
# provisioning that Stage 2 will add. Stage 1 deliberately ships no `.tf` files:
# an infrastructure module that has never been planned would be reported as
# verified configuration when it is not.
#
# Stage 2 target topology
# -----------------------
#   * VCN + public subnet with a security list that admits SSH and the swarm
#     task API only from the operator /32, never 0.0.0.0/0.
#   * One inference node sized for a quantised 3B–8B model in RAM
#     (Ampere A1.Flex 4 OCPU / 24 GB, or a GPU shape when one is available).
#   * One orchestrator-capable node, or the same node with a strict resource
#     envelope; the Compose limits in `docker/docker-compose.yml` are the
#     contract the shape must satisfy.
#   * A persistent block volume mounted at /mnt/llm-swarm for model weights,
#     agent state and run records, with a labelled filesystem and a `nofail`
#     fstab entry so the swarm survives reboot and host replacement.
#   * cloud-init that installs Docker, mounts the volume and starts the stack.
#
# Exit criteria for Stage 2
# -------------------------
#   tofu fmt -recursive -check
#   tofu init && tofu validate
#   tofu test                    (module assertions, no cloud credentials)
#   tofu plan                    (read-only; resource count reviewed)
#
# Nothing in this directory has been applied. `terraform.tfvars.example` holds
# placeholders only; the real `terraform.tfvars` is gitignored.
