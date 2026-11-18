# =============================================================================
# Multi-Agent Local LLM Swarm — OpenTofu contract
# =============================================================================
# OCI is the validated target. The provider constraint accepts patch and minor
# security fixes while refusing a silent major-version change.
#
# PORTABILITY
# -----------
# The root interface (variables, outputs, module boundaries) is provider
# neutral. To target another cloud, replace `provider.tf` and the three module
# bodies; keep `variables.tf`, `outputs.tf` and `main.tf` wiring intact:
#
#   AWS: provider "aws"    { region = var.region }
#        network -> aws_vpc, aws_internet_gateway, aws_route_table,
#                   aws_security_group, aws_subnet
#        compute -> aws_instance (t4g.xlarge for the ARM profile)
#        storage -> aws_ebs_volume (gp3) + aws_volume_attachment
#
#   GCP: provider "google" { project = var.gcp_project, region = var.region }
#        network -> google_compute_network, google_compute_subnetwork,
#                   google_compute_firewall
#        compute -> google_compute_instance (t2a-standard-4, Tau/Axion ARM)
#        storage -> google_compute_disk (pd-balanced) +
#                   google_compute_attached_disk
#
# STATE
# -----
# Local state is the default and is gitignored (`*.tfstate`, `.terraform/`).
# A shared environment should use a remote backend with locking. OCI Object
# Storage supports the S3-compatible backend:
#
# backend "s3" {
#   endpoints = { s3 = "https://<namespace>.compat.objectstorage.<region>.oraclecloud.com" }
#   bucket    = "cloudguard-llm-swarm-tfstate"
#   key       = "llm-swarm/terraform.tfstate"
#   region    = "eu-frankfurt-1"
#   skip_region_validation      = true
#   skip_credentials_validation = true
#   skip_requesting_account_id  = true
#   skip_s3_checksum            = true
# }
#
# AWS S3 + DynamoDB and GCS (`backend "gcs"`) are drop-in equivalents. Never
# commit a state file: it contains resource identifiers and, for some providers,
# attribute values that are effectively secrets.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 6.20.0, < 8.0.0"
    }
  }
}
