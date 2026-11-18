# =============================================================================
# OCI compute module — high-memory instance for the multi-agent swarm
# =============================================================================
# Default profile: VM.Standard.A1.Flex, 4 OCPU / 24 GB (Ampere ARM). That is the
# Always Free ceiling and enough resident memory for a quantised 3B model at 4k
# context plus the orchestrator and Docker. VM.Standard3.Flex (x86, up to 64 GB+)
# or a larger A1 allocation is the credit-funded step-up; switch shape and
# sizing in tfvars.
#
# PORTABILITY
#
#   | OCI (here)                          | AWS                                  | GCP                                     |
#   | ----------------------------------- | ------------------------------------ | --------------------------------------- |
#   | oci_core_instance                   | aws_instance                         | google_compute_instance                 |
#   | VM.Standard.A1.Flex (ARM)           | t4g.xlarge (Graviton)                | t2a-standard-4 (Axion/Tau)              |
#   | shape_config { ocpus, memory_gbs }  | instance_type                        | machine_type                            |
#   | create_vnic_details                 | subnet_id + public IP association    | network_interface { access_config {} }  |
#   | metadata.user_data (base64)         | user_data                            | metadata_startup_script                 |
#   | availability_domain                 | availability_zone                    | zone                                    |
#
# The image lookup is the only provider-specific detail: OCI resolves an Ubuntu
# image per shape architecture, AWS uses an AMI id, GCP uses an image family
# such as `ubuntu-2404-lts-arm64`.

locals {
  is_flex_shape = can(regex("Flex$", var.instance_shape))

  # A pinned AD wins; otherwise the first AD. A1 capacity is often exhausted in
  # AD-1 — pin AD-2/AD-3 in tfvars when the apply reports out of host capacity.
  availability_domain = coalesce(
    var.availability_domain,
    data.oci_identity_availability_domains.ads.availability_domains[0].name
  )

  resolve_image     = var.image_id == null
  resolved_image_id = local.resolve_image ? data.oci_core_images.ubuntu[0].images[0].id : var.image_id
}

data "oci_identity_availability_domains" "ads" {
  # Availability domains are tenancy-scoped.
  compartment_id = var.tenancy_ocid
}

data "oci_core_images" "ubuntu" {
  count = local.resolve_image ? 1 : 0

  compartment_id           = var.compartment_id
  operating_system         = var.image_operating_system
  operating_system_version = var.image_os_version
  # Filtering by shape returns the aarch64 build for A1 and the x86_64 build
  # for E-series shapes.
  shape      = var.instance_shape
  sort_by    = "TIMECREATED"
  sort_order = "DESC"
}

resource "oci_core_instance" "this" {
  compartment_id      = var.compartment_id
  availability_domain = local.availability_domain
  display_name        = "${var.name_prefix}-vm"
  shape               = var.instance_shape
  freeform_tags       = var.freeform_tags

  # Flex shapes require shape_config; fixed shapes reject it.
  dynamic "shape_config" {
    for_each = local.is_flex_shape ? [1] : []

    content {
      ocpus         = var.instance_ocpus
      memory_in_gbs = var.instance_memory_gbs
    }
  }

  source_details {
    source_type             = "image"
    source_id               = local.resolved_image_id
    boot_volume_size_in_gbs = var.boot_volume_size_gbs
  }

  create_vnic_details {
    subnet_id        = var.subnet_id
    assign_public_ip = true
    display_name     = "${var.name_prefix}-vnic"
    hostname_label   = var.hostname_label
  }

  # user_data is cloud-init: it mounts the block volume and prepares the data
  # directories. It never carries a credential, an API key or a private key —
  # instance metadata is readable by anything running on the host.
  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data           = var.user_data
  }

  timeouts {
    create = "30m"
  }
}

# Read the address from the primary VNIC rather than deprecated attributes.
data "oci_core_vnic_attachments" "this" {
  compartment_id = var.compartment_id
  instance_id    = oci_core_instance.this.id
}

data "oci_core_vnic" "primary" {
  vnic_id = data.oci_core_vnic_attachments.this.vnic_attachments[0].vnic_id
}
