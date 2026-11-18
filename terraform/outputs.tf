# =============================================================================
# Outputs — what an operator or the next stage needs
# =============================================================================
# Public information only: addresses, OCIDs, sizes and an exposure inventory.
# Never a credential, a private key or a command containing a secret.
#
# PORTABILITY: names stay neutral — `instance_public_ip` is an EC2 public IP or
# a GCE external IP; `block_volume_*` is an EBS volume or a Persistent Disk.

output "instance_public_ip" {
  description = "Public IPv4 address of the swarm host."
  value       = module.compute.public_ip
}

output "instance_private_ip" {
  description = "Private IPv4 address inside the swarm VCN."
  value       = module.compute.private_ip
}

output "instance_id" {
  description = "OCID of the compute instance."
  value       = module.compute.instance_id
}

output "instance_shape" {
  description = "Shape used, with flexible sizing when applicable."
  value       = "${var.instance_shape} (${var.instance_ocpus} OCPU / ${var.instance_memory_gbs} GB)"
}

output "availability_domain" {
  description = "Availability domain the instance landed in; pin it when A1 capacity is tight."
  value       = module.compute.availability_domain
}

output "image_id" {
  description = "Image OCID used at apply time."
  value       = module.compute.image_id
}

# -----------------------------------------------------------------------------
# Persistent block storage
# -----------------------------------------------------------------------------

output "block_volume_id" {
  description = "OCID of the persistent model-weight and agent-state volume."
  value       = module.storage.volume_id
}

output "block_volume_attachment_id" {
  description = "OCID of the volume attachment."
  value       = module.storage.attachment_id
}

output "block_volume_size_gbs" {
  description = "Size of the data volume in GB."
  value       = module.storage.size_in_gbs
}

output "block_volume_vpus_per_gb" {
  description = "Performance units per GB of the data volume."
  value       = module.storage.vpus_per_gb
}

output "block_volume_device" {
  description = "Guest device path OCI reports for the attachment (paravirtualized: /dev/oracleoci/oraclevdb)."
  value       = module.storage.device
}

output "block_volume_mount_point" {
  description = "Host path where cloud-init mounts the data volume."
  value       = module.storage.mount_point
}

# -----------------------------------------------------------------------------
# Network and exposure
# -----------------------------------------------------------------------------

output "vcn_id" {
  description = "OCID of the swarm VCN."
  value       = module.network.vcn_id
}

output "subnet_id" {
  description = "OCID of the public subnet."
  value       = module.network.subnet_id
}

output "security_list_id" {
  description = "OCID of the security list enforcing the perimeter."
  value       = module.network.security_list_id
}

output "ingress_ports" {
  description = "Inbound TCP ports actually opened (22 plus the configured inference/API ports)."
  value       = module.network.ingress_ports
}

output "ingress_exposure" {
  description = "Port/source pairs for an explicit exposure review. A world-open source here is a finding, not a setting."
  value       = module.network.ingress_sources
}

output "swarm_endpoints" {
  description = "Endpoints the orchestrator and operators use once the stack is deployed on the host."
  value = {
    # Compose publishes both ports on 127.0.0.1 by default. Reaching them from
    # the operator workstation is an SSH tunnel, not a public binding:
    #   ssh -N -L 8090:127.0.0.1:8090 -L 11434:127.0.0.1:11434 ubuntu@<ip>
    task_api        = "http://127.0.0.1:8090"
    llm_openai_base = "http://127.0.0.1:11434/v1"
  }
}

output "ssh_connection" {
  description = "Copy-paste SSH command. The private key stays on the operator workstation."
  value       = "ssh ubuntu@${module.compute.public_ip}"
}

output "deploy_next_step" {
  description = "What to do after apply. Carries no credential: .env is created on the host."
  value       = <<-EOT
    1. ssh ubuntu@${module.compute.public_ip}
    2. df -h ${module.storage.mount_point}          # confirm the data volume is mounted
    3. git clone https://github.com/kaveh8866/Multi-Agent-Local-LLM-Swarm.git llm-swarm
    4. cd llm-swarm && cp .env.example .env          # set the orchestrator token locally
    5. docker compose --env-file .env -f docker/docker-compose.yml up -d --build
    6. docker compose --env-file .env -f docker/docker-compose.yml \
         exec llm-runtime ollama pull qwen2.5:3b-instruct
  EOT
}
