# =============================================================================
# OCI block storage module — persistent volume for weights and agent state
# =============================================================================
# The volume holds model weights, inference caches and agent run records, so
# they survive instance reboot, instance replacement and rollback. It lives in
# the same availability domain as the instance (a block volume cannot cross ADs).
#
# PORTABILITY
#
#   | OCI (here)                     | AWS                                  | GCP                                    |
#   | ------------------------------ | ------------------------------------ | -------------------------------------- |
#   | oci_core_volume                | aws_ebs_volume (gp3)                 | google_compute_disk (pd-balanced)      |
#   | oci_core_volume_attachment     | aws_volume_attachment                | google_compute_attached_disk           |
#   | vpus_per_gb (0..120)           | iops / throughput on gp3             | disk type (pd-balanced/pd-ssd)         |
#   | paravirtualized -> oraclevdb   | /dev/xvdf or /dev/nvme1n1            | /dev/disk/by-id/google-<name>          |
#   | availability_domain            | availability_zone                    | zone                                   |
#
# `size_in_gbs`, `attachment_type` and `mount_point` are the portable inputs;
# only this file names OCI resources.
#
# DATA SAFETY
#   - The volume is independent of the instance lifecycle: destroying the
#     instance does not delete it.
#   - `tofu destroy` DOES delete it, and model weights are expensive to
#     re-download. For a long-lived swarm, set
#     `lifecycle { prevent_destroy = true }` on the volume before the first real
#     model lands, and remove it deliberately for teardown.

resource "oci_core_volume" "llm" {
  compartment_id      = var.compartment_id
  availability_domain = var.availability_domain
  display_name        = "${var.name_prefix}-llm-data"
  size_in_gbs         = var.size_in_gbs
  vpus_per_gb         = var.vpus_per_gb
  freeform_tags       = var.freeform_tags
}

resource "oci_core_volume_attachment" "llm" {
  attachment_type = var.attachment_type
  instance_id     = var.instance_id
  volume_id       = oci_core_volume.llm.id
  display_name    = "${var.name_prefix}-llm-data-attachment"
  is_read_only    = false
  # A paravirtualized attachment is visible in the guest as
  # /dev/oracleoci/oraclevdb (first data volume) with no guest agent required.
  # iscsi exposes multiple paths and needs multipathd; cloud-init prefers the
  # paravirtualized path.
}
