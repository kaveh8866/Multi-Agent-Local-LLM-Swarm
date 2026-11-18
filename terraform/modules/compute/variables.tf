variable "compartment_id" {
  description = "Compartment that owns the instance."
  type        = string
}

variable "tenancy_ocid" {
  description = "Tenancy OCID used for the availability-domain lookup."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for instance and VNIC names."
  type        = string
}

variable "subnet_id" {
  description = "Subnet OCID where the primary VNIC is created."
  type        = string
}

variable "availability_domain" {
  description = "Optional availability domain; null selects the first AD."
  type        = string
  default     = null
}

variable "instance_shape" {
  description = "OCI compute shape."
  type        = string
}

variable "instance_ocpus" {
  description = "OCPUs for flexible shapes."
  type        = number
}

variable "instance_memory_gbs" {
  description = "Memory in GB for flexible shapes."
  type        = number
}

variable "boot_volume_size_gbs" {
  description = "Boot volume size in GB."
  type        = number
}

variable "hostname_label" {
  description = "VNIC hostname label."
  type        = string
}

variable "ssh_public_key" {
  description = "OpenSSH public key content for instance metadata."
  type        = string
}

variable "user_data" {
  description = "Base64-encoded cloud-init user data."
  type        = string
}

variable "image_id" {
  description = "Optional pinned image OCID; null resolves the newest compatible Ubuntu image."
  type        = string
  default     = null
}

variable "image_operating_system" {
  description = "Operating system used for image resolution."
  type        = string
  default     = "Canonical Ubuntu"
}

variable "image_os_version" {
  description = "Operating system version used for image resolution."
  type        = string
  default     = "24.04"
}

variable "freeform_tags" {
  description = "Freeform tags applied to the instance."
  type        = map(string)
}
