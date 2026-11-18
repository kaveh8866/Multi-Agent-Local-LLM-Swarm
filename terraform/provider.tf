# =============================================================================
# OCI provider
# =============================================================================
# Authentication precedence:
#   1. `config_file_profile` (default DEFAULT) reads ~/.oci/config and the API
#      signing key path it references. No key material enters the repository,
#      the plan output or the state file.
#   2. Inline `user_ocid` / `fingerprint` / `private_key_path` for a CI runner
#      that materialises a key at runtime from its secret store.
#
# PORTABILITY
#   AWS: provider "aws"    { region = var.region }
#        credentials come from the standard chain (env, shared config, IMDS).
#   GCP: provider "google" { project = var.gcp_project, region = var.region }
#        credentials come from ADC (`gcloud auth application-default login`).
# Only this file names a provider-specific auth mechanism.

provider "oci" {
  tenancy_ocid        = var.tenancy_ocid
  region              = var.region
  config_file_profile = var.config_file_profile

  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
}
