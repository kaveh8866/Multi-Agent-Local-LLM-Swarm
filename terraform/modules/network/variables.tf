variable "compartment_id" {
  description = "Compartment that owns the VCN and its children."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for every network resource name."
  type        = string
}

variable "vcn_cidr" {
  description = "VCN IPv4 CIDR."
  type        = string
}

variable "subnet_cidr" {
  description = "Public subnet IPv4 CIDR."
  type        = string
}

variable "ssh_allowed_cidrs" {
  description = "Source ranges allowed to reach TCP/22."
  type        = list(string)
}

variable "llm_api_cidrs" {
  description = "Source ranges allowed to reach the inference and task-API ports."
  type        = list(string)
}

variable "llm_ports" {
  description = "TCP ports (inference server, task API) to admit from llm_api_cidrs."
  type        = list(number)
}

variable "vcn_dns_label" {
  description = "VCN DNS label (alphanumeric, max 15 characters)."
  type        = string
}

variable "subnet_dns_label" {
  description = "Subnet DNS label (alphanumeric, max 15 characters)."
  type        = string
}

variable "freeform_tags" {
  description = "Freeform tags applied to every network resource."
  type        = map(string)
}
