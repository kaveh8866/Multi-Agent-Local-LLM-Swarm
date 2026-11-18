variable "compartment_id" {
  description = "Compartment that owns the block volume."
  type        = string
}

variable "availability_domain" {
  description = "Availability domain of the instance; a block volume cannot cross ADs."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for the volume and attachment names."
  type        = string
}

variable "instance_id" {
  description = "Instance OCID the volume is attached to."
  type        = string
}

variable "size_in_gbs" {
  description = "Volume size in GB."
  type        = number
}

variable "vpus_per_gb" {
  description = "Performance units per GB (0 = Lower Cost, 10 = Balanced)."
  type        = number
}

variable "attachment_type" {
  description = "paravirtualized or iscsi."
  type        = string
}

variable "mount_point" {
  description = "Absolute host path where cloud-init mounts the volume."
  type        = string
}

variable "freeform_tags" {
  description = "Freeform tags applied to the volume."
  type        = map(string)
}
