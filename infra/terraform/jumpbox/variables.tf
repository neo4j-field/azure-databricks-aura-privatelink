variable "resource_group_name" {
  description = "Resource group for the Bastion host, public IP, and jump box VM."
  type        = string
}

variable "location" {
  description = "Azure region. Must match the region of the consumer VNet."
  type        = string
}

variable "vnet_name" {
  description = "Name of the consumer VNet where the jump box and Bastion will be deployed."
  type        = string
}

variable "vnet_resource_group" {
  description = "Resource group of the consumer VNet. Defaults to resource_group_name."
  type        = string
  default     = null
  nullable    = true
}

variable "jump_subnet_name" {
  description = "Name of an existing subnet in the VNet for the jump box VM. A /28 (14 usable IPs) is sufficient."
  type        = string
}

variable "bastion_subnet_cidr" {
  description = "CIDR for the AzureBastionSubnet. Must be /26 or larger. Must not overlap existing subnets."
  type        = string

  validation {
    condition     = can(cidrnetmask(var.bastion_subnet_cidr)) && tonumber(split("/", var.bastion_subnet_cidr)[1]) <= 26
    error_message = "bastion_subnet_cidr must be a valid CIDR and /26 or larger (e.g. 10.0.255.0/26)."
  }
}

variable "pe_nic_ip" {
  description = "Private IP of the Neo4j Aura Private Endpoint NIC. The jump box socat proxies forward here. Pass module.private_endpoint.private_endpoint_nic_ip."
  type        = string
}

variable "neo4j_ports" {
  description = "TCP ports to proxy from the jump box to the Private Endpoint NIC."
  type        = list(number)
  default     = [7687, 7474, 7473, 8491]
}

variable "jumpbox_name" {
  description = "Name of the jump box VM."
  type        = string
  default     = "neo4j-jumpbox"
}

variable "bastion_name" {
  description = "Name of the Azure Bastion host."
  type        = string
  default     = "neo4j-bastion"
}

variable "vm_size" {
  description = "VM size for the jump box. Standard_B1s is sufficient for a developer proxy."
  type        = string
  default     = "Standard_B1s"
}

variable "admin_username" {
  description = "Admin username for the jump box VM."
  type        = string
  default     = "azureuser"
}

variable "ssh_public_key" {
  description = "SSH public key for the jump box VM admin user. The private key is used in the gcloud ssh step."
  type        = string
}

variable "tags" {
  description = "Tags applied to all resources in this module."
  type        = map(string)
  default = {
    project    = "neo4j-aura-privatelink"
    managed_by = "terraform"
  }
}
