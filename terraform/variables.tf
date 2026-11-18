# =============================================================================
# Input contract — Multi-Agent Local LLM Swarm
# =============================================================================
# Names stay provider neutral so the module bodies can be swapped:
#
#   region                          -> AWS region / GCP region
#   vcn_cidr, subnet_cidr           -> VPC CIDR / subnetwork range
#   ssh_allowed_cidrs               -> security-group or firewall source ranges
#   llm_api_cidrs, llm_ports        -> source ranges and ports for inference/API
#   instance_shape + ocpus/memory   -> instance type / machine type
#   block_volume_size_gbs           -> EBS volume size / Persistent Disk size
#   availability_domain             -> AWS AZ / GCP zone
#
# Environment-specific values belong in terraform.tfvars (gitignored), never in
# a module and never in this repository.

# -----------------------------------------------------------------------------
# OCI identity
# -----------------------------------------------------------------------------

variable "tenancy_ocid" {
  description = "OCI tenancy OCID. Also scopes the availability-domain lookup."
  type        = string

  validation {
    condition     = can(regex("^ocid1\\.tenancy\\.", var.tenancy_ocid))
    error_message = "tenancy_ocid must be an OCI tenancy OCID."
  }
}

variable "compartment_ocid" {
  description = "Compartment OCID that owns the VCN, instance, block volume and their children."
  type        = string

  validation {
    condition     = can(regex("^ocid1\\.(compartment|tenancy)\\.", var.compartment_ocid))
    error_message = "compartment_ocid must be an OCI compartment (or tenancy) OCID."
  }
}

variable "region" {
  description = "OCI region identifier, e.g. eu-frankfurt-1."
  type        = string
  default     = "eu-frankfurt-1"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.region))
    error_message = "region must be an OCI region identifier such as eu-frankfurt-1."
  }
}

variable "user_ocid" {
  description = "OCI user OCID for inline API-key auth. Null uses config_file_profile."
  type        = string
  default     = null
}

variable "fingerprint" {
  description = "Fingerprint of the OCI API signing key. Null uses config_file_profile."
  type        = string
  default     = null
}

variable "private_key_path" {
  description = "Path to the OCI API signing key outside this repository. Null uses config_file_profile."
  type        = string
  default     = null
}

variable "config_file_profile" {
  description = "Profile inside ~/.oci/config. Null uses the provider default."
  type        = string
  default     = null
}

# -----------------------------------------------------------------------------
# Naming and metadata
# -----------------------------------------------------------------------------

variable "project_name" {
  description = "Short slug used to prefix every resource name and the on-host mount layout."
  type        = string
  default     = "llm-swarm"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.project_name))
    error_message = "project_name must be lowercase alphanumeric with hyphens, 2-31 characters."
  }
}

variable "environment" {
  description = "Environment suffix (dev, staging, prod)."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,15}$", var.environment))
    error_message = "environment must be lowercase alphanumeric with hyphens."
  }
}

variable "freeform_tags" {
  description = "Freeform tags applied to every resource (cost tracking and CloudGuard metadata)."
  type        = map(string)
  default = {
    project  = "multi-agent-local-llm-swarm"
    owner    = "cloudguard"
    tier     = "high-spec"
    workload = "local-llm-agents"
  }
}

# -----------------------------------------------------------------------------
# Network
# -----------------------------------------------------------------------------

variable "vcn_cidr" {
  description = "CIDR for the swarm VCN. Deliberately disjoint from the other portfolio projects."
  type        = string
  default     = "10.10.0.0/16"

  validation {
    condition     = can(cidrhost(var.vcn_cidr, 0))
    error_message = "vcn_cidr must be a valid IPv4 CIDR block."
  }
}

variable "subnet_cidr" {
  description = "CIDR for the public subnet. Must sit inside vcn_cidr."
  type        = string
  default     = "10.10.1.0/24"

  validation {
    condition     = can(cidrhost(var.subnet_cidr, 0))
    error_message = "subnet_cidr must be a valid IPv4 CIDR block."
  }
}

variable "ssh_allowed_cidrs" {
  description = <<-EOT
    Source ranges allowed to reach TCP/22. Use the operator /32 or a VPN range.
    A world-open source is rejected: the management plane is never public.
  EOT
  type        = list(string)
  default     = ["203.0.113.10/32"] # RFC 5737 documentation range placeholder

  validation {
    condition = (
      length(var.ssh_allowed_cidrs) > 0 &&
      alltrue([for cidr in var.ssh_allowed_cidrs : can(cidrhost(cidr, 0))]) &&
      !contains(var.ssh_allowed_cidrs, "0.0.0.0/0") &&
      !contains(var.ssh_allowed_cidrs, "::/0")
    )
    error_message = "ssh_allowed_cidrs must be one or more specific IPv4 CIDRs (for example 203.0.113.10/32), never 0.0.0.0/0."
  }
}

variable "llm_api_cidrs" {
  description = <<-EOT
    Source ranges allowed to reach the inference and task-API ports. Keep this
    to the operator /32 or a private VPN range. The API authenticates with a
    bearer token but runs over plain HTTP, so it is never exposed to the
    internet.
  EOT
  type        = list(string)
  default     = ["203.0.113.10/32"] # RFC 5737 documentation range placeholder

  validation {
    condition = (
      length(var.llm_api_cidrs) > 0 &&
      alltrue([for cidr in var.llm_api_cidrs : can(cidrhost(cidr, 0))]) &&
      !contains(var.llm_api_cidrs, "0.0.0.0/0") &&
      !contains(var.llm_api_cidrs, "::/0")
    )
    error_message = "llm_api_cidrs must be one or more specific IPv4 CIDRs, never 0.0.0.0/0."
  }
}

variable "llm_ports" {
  description = <<-EOT
    TCP ports admitted from llm_api_cidrs.

    Ollama OpenAI-compatible API: 11434 (Compose publishes it on loopback; the
    security list is the second gate when the port is bound to the host).
    Swarm task API: 8090 (host mapping of the orchestrator's internal 8080).
    vLLM on a GPU node: 8000 if the backend is moved to its native port.

    List only ports that something actually listens on.
  EOT
  type        = list(number)
  default     = [11434, 8090]

  validation {
    condition = (
      length(var.llm_ports) > 0 &&
      alltrue([for port in var.llm_ports : port >= 1 && port <= 65535])
    )
    error_message = "llm_ports must contain TCP port numbers between 1 and 65535."
  }
}

# -----------------------------------------------------------------------------
# Compute
# -----------------------------------------------------------------------------

variable "instance_shape" {
  description = <<-EOT
    OCI compute shape. VM.Standard.A1.Flex (Ampere ARM, Always Free eligible up
    to 4 OCPU / 24 GB) is the default. For a larger quantised model, raise
    instance_ocpus/instance_memory_gbs against the credit allocation, or switch
    to VM.Standard3.Flex / VM.Standard.E5.Flex for x86.
  EOT
  type        = string
  default     = "VM.Standard.A1.Flex"

  validation {
    condition     = can(regex("^VM\\.Standard\\.[A-Za-z0-9.]+$", var.instance_shape))
    error_message = "instance_shape must look like VM.Standard.A1.Flex or VM.Standard3.Flex."
  }
}

variable "instance_ocpus" {
  description = "OCPUs for flexible shapes. 4 is the Always Free A1 ceiling; more is credit-funded."
  type        = number
  default     = 4

  validation {
    condition     = var.instance_ocpus >= 1 && var.instance_ocpus <= 128
    error_message = "instance_ocpus must be between 1 and 128."
  }
}

variable "instance_memory_gbs" {
  description = <<-EOT
    Memory in GB for flexible shapes. 24 GB is the Always Free A1 ceiling and
    holds a 3B model at 4k context with room for the orchestrator. 48–64 GB is
    the credit-funded step-up for an 8B model.
  EOT
  type        = number
  default     = 24

  validation {
    condition     = var.instance_memory_gbs >= 8 && var.instance_memory_gbs <= 512
    error_message = "instance_memory_gbs must be between 8 and 512; a local model needs residency headroom."
  }
}

variable "boot_volume_size_gbs" {
  description = "Boot volume size in GB. Holds Ubuntu, Docker and container images, not model weights."
  type        = number
  default     = 100

  validation {
    condition     = var.boot_volume_size_gbs >= 50 && var.boot_volume_size_gbs <= 500
    error_message = "boot_volume_size_gbs must be between 50 and 500."
  }
}

variable "availability_domain" {
  description = "Availability domain name. Null uses the first AD; pin another AD when A1 reports out of host capacity."
  type        = string
  default     = null
}

variable "hostname_label" {
  description = "VNIC hostname label (alphanumeric, starts with a letter, max 15 characters)."
  type        = string
  default     = "llmswarm"

  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9]{0,14}$", var.hostname_label))
    error_message = "hostname_label must be alphanumeric, start with a letter and be at most 15 characters."
  }
}

variable "ssh_public_key" {
  description = "SSH public key content (not a path, not a private key) injected into instance metadata."
  type        = string

  validation {
    condition     = can(regex("^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)) ", var.ssh_public_key))
    error_message = "ssh_public_key must be an OpenSSH public key beginning with ssh-ed25519, ssh-rsa or ecdsa-sha2-*. Never paste a private key."
  }
}

variable "image_id" {
  description = "Pin a specific image OCID. Null resolves the newest Ubuntu build for the shape."
  type        = string
  default     = null
}

variable "image_operating_system" {
  description = "Operating system resolved when image_id is null."
  type        = string
  default     = "Canonical Ubuntu"
}

variable "image_operating_system_version" {
  description = "OS version resolved when image_id is null."
  type        = string
  default     = "24.04"
}

# -----------------------------------------------------------------------------
# Persistent block storage
# -----------------------------------------------------------------------------

variable "block_volume_size_gbs" {
  description = <<-EOT
    Persistent block volume for model weights, caches and agent state. 200 GB
    holds several quantised 3B–8B models plus run records. The Always Free
    allowance is 200 GB TOTAL, so boot 100 + data 200 consumes trial credit.
  EOT
  type        = number
  default     = 200

  validation {
    condition     = var.block_volume_size_gbs >= 50 && var.block_volume_size_gbs <= 32768
    error_message = "block_volume_size_gbs must be between 50 and 32768."
  }
}

variable "block_volume_vpus_per_gb" {
  description = <<-EOT
    Performance units per GB for the block volume. 0 = Lower Cost, 10 =
    Balanced (default), up to 120 = Ultra High. Model loading is a sequential
    read; inference is memory-bound, not disk-bound.
  EOT
  type        = number
  default     = 10

  validation {
    condition     = contains([0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120], var.block_volume_vpus_per_gb)
    error_message = "block_volume_vpus_per_gb must be 0 (Lower Cost) or a multiple of 10 up to 120."
  }
}

variable "volume_attachment_type" {
  description = "paravirtualized (default, no guest agent needed) or iscsi (multipath, higher throughput)."
  type        = string
  default     = "paravirtualized"

  validation {
    condition     = contains(["paravirtualized", "iscsi"], var.volume_attachment_type)
    error_message = "volume_attachment_type must be paravirtualized or iscsi."
  }
}

variable "volume_mount_point" {
  description = "Absolute path where cloud-init mounts the data volume on the host."
  type        = string
  default     = "/mnt/llm-swarm"

  validation {
    condition     = can(regex("^/[A-Za-z0-9/_-]+$", var.volume_mount_point))
    error_message = "volume_mount_point must be an absolute path without spaces."
  }
}
