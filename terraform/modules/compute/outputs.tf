output "instance_id" {
  description = "OCID of the compute instance."
  value       = oci_core_instance.this.id
}

output "public_ip" {
  description = "Public IPv4 address of the instance."
  value       = data.oci_core_vnic.primary.public_ip_address
}

output "private_ip" {
  description = "Private IPv4 address of the instance."
  value       = data.oci_core_vnic.primary.private_ip_address
}

output "availability_domain" {
  description = "Availability domain actually used."
  value       = local.availability_domain
}

output "image_id" {
  description = "Image OCID used at apply time."
  value       = local.resolved_image_id
}
