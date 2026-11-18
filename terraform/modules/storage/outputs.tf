output "volume_id" {
  description = "OCID of the persistent block volume."
  value       = oci_core_volume.llm.id
}

output "attachment_id" {
  description = "OCID of the volume attachment."
  value       = oci_core_volume_attachment.llm.id
}

output "device" {
  description = "Guest device path reported by OCI for the attachment."
  value       = oci_core_volume_attachment.llm.device
}

output "size_in_gbs" {
  description = "Configured volume size in GB."
  value       = oci_core_volume.llm.size_in_gbs
}

output "vpus_per_gb" {
  description = "Configured performance units per GB."
  value       = oci_core_volume.llm.vpus_per_gb
}

output "mount_point" {
  description = "Host mount point prepared by cloud-init."
  value       = var.mount_point
}
