# =============================================================================
# Root module — multi-agent swarm infrastructure on OCI
# =============================================================================
# Three modules, one contract:
#   network  VCN, internet gateway, route table, security list, public subnet
#   compute  Ampere A1.Flex instance (4 OCPU / 24 GB by default), Ubuntu image
#   storage  persistent block volume attached at a stable mount point
#
# PORTABILITY (the swap lives in the module bodies, not here):
#
#   OCI VCN + IGW + route table + security list + subnet
#     ~ AWS VPC + IGW + route table + security group + subnet
#     ~ GCP VPC network + implicit route + firewall rule + subnetwork
#
#   OCI oci_core_instance (VM.Standard.A1.Flex)
#     ~ AWS aws_instance (t4g.xlarge, Graviton ARM)
#     ~ GCP google_compute_instance (t2a-standard-4, Tau/Axion ARM)
#
#   OCI oci_core_volume + volume_attachment (persistent block storage)
#     ~ AWS aws_ebs_volume + aws_volume_attachment (gp3)
#     ~ GCP google_compute_disk + google_compute_attached_disk (pd-balanced)
#
#   OCI metadata.user_data (base64 cloud-init)
#     ~ AWS user_data (plain text)
#     ~ GCP metadata_startup_script
#
# Every variable and output in this root keeps a provider-neutral name, so a
# port replaces `provider.tf` and the three module implementations only.

locals {
  name_prefix = "${var.project_name}-${var.environment}"

  tags = merge(var.freeform_tags, {
    environment = var.environment
    managed_by  = "opentofu"
    stage       = "2-iac"
  })

  # ext4 label used by cloud-init to re-mount the volume after a reboot. Labels
  # are limited to 16 characters and no hyphen is used.
  volume_label = "llmswarm"

  # Cloud-init prepares the host only: it discovers, formats (first boot only)
  # and mounts the data volume, then creates the directory layout the compose
  # bind mounts expect. It deliberately carries no credential, no API key and no
  # .env — instance metadata is readable from inside the guest.
  user_data = base64encode(
    templatefile("${path.module}/cloud-init.yaml.tftpl", {
      project_name = var.project_name
      mount_point  = var.volume_mount_point
      volume_label = local.volume_label
    })
  )
}

module "network" {
  source = "./modules/network"

  # Pin the provider address. Without this, OpenTofu infers hashicorp/oci for
  # the child while the root uses oracle/oci, and apply fails on a tenancy
  # configuration mismatch.
  providers = { oci = oci }

  compartment_id    = var.compartment_ocid
  name_prefix       = local.name_prefix
  vcn_cidr          = var.vcn_cidr
  subnet_cidr       = var.subnet_cidr
  ssh_allowed_cidrs = var.ssh_allowed_cidrs
  llm_api_cidrs     = var.llm_api_cidrs
  llm_ports         = var.llm_ports
  vcn_dns_label     = "llmswarm"
  subnet_dns_label  = "swarmsubnet"
  freeform_tags     = local.tags
}

module "compute" {
  source = "./modules/compute"

  providers = { oci = oci }

  compartment_id         = var.compartment_ocid
  tenancy_ocid           = var.tenancy_ocid
  name_prefix            = local.name_prefix
  subnet_id              = module.network.subnet_id
  availability_domain    = var.availability_domain
  instance_shape         = var.instance_shape
  instance_ocpus         = var.instance_ocpus
  instance_memory_gbs    = var.instance_memory_gbs
  boot_volume_size_gbs   = var.boot_volume_size_gbs
  hostname_label         = var.hostname_label
  ssh_public_key         = var.ssh_public_key
  user_data              = local.user_data
  image_id               = var.image_id
  image_operating_system = var.image_operating_system
  image_os_version       = var.image_operating_system_version
  freeform_tags          = local.tags
}

module "storage" {
  source = "./modules/storage"

  providers = { oci = oci }

  # The volume must land in the instance's availability domain, which is only
  # known after the compute module resolves it. depends_on keeps the instance
  # (and therefore the AD) resolved first.
  depends_on = [module.compute]

  compartment_id      = var.compartment_ocid
  availability_domain = module.compute.availability_domain
  name_prefix         = local.name_prefix
  instance_id         = module.compute.instance_id
  size_in_gbs         = var.block_volume_size_gbs
  vpus_per_gb         = var.block_volume_vpus_per_gb
  attachment_type     = var.volume_attachment_type
  mount_point         = var.volume_mount_point
  freeform_tags       = local.tags
}
