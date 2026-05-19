variable "azure_subscription_id" {
  description = "Azure subscription where the private endpoint and DNS zone will live. Must be registered in the Aura Network Access config before applying."
  type        = string
}

variable "azure_tenant_id" {
  description = "Azure AD tenant ID for the subscription."
  type        = string
}

variable "resource_group_name" {
  description = "Existing resource group that will hold the private endpoint, NIC, and (optionally) the private DNS zone."
  type        = string
}

variable "virtual_network_name" {
  description = "Existing VNet that the private endpoint subnet belongs to."
  type        = string
}

variable "vnet_resource_group_name" {
  description = "Resource group of the VNet, if different from `resource_group_name`. Defaults to the same RG."
  type        = string
  default     = null
  nullable    = true
}

variable "pe_subnet_name" {
  description = "Subnet that will host the private endpoint NIC. The subnet must have `privateEndpointNetworkPolicies` disabled (set automatically in modern Azure subnets)."
  type        = string
}

variable "private_endpoint_name" {
  description = "Name for the private endpoint resource. Recommended pattern: pe-<aura-instance-id>-<region>."
  type        = string
}

variable "aura_pls_alias" {
  description = "Neo4j Aura Private Link Service alias from the Aura console. Example: production-orch-0477-service.<guid>.<region>.azure.privatelinkservice"
  type        = string
}

variable "aura_instance_id" {
  description = "Aura instance id (the host label of the hostname, e.g. `b7253d3b` from `b7253d3b.databases.neo4j.io`). Used to create the A record in the private DNS zone."
  type        = string
}

variable "manage_private_dns" {
  description = "If true, this stack creates the `databases.neo4j.io` private DNS zone, links it to the VNet, and writes the Aura A record. Set to false if you manage DNS centrally and want to wire the zone yourself."
  type        = bool
  default     = true
}

variable "connection_request_message" {
  description = "Free-form message attached to the PLS connection request. Shows up on the Aura approval screen — make it identifying so the Aura admin can match the request."
  type        = string
  default     = "Databricks-adjacent workloads connecting to Neo4j Aura via Azure PrivateLink."
}

variable "tags" {
  description = "Tags applied to created Azure resources."
  type        = map(string)
  default = {
    project    = "neo4j-aura-privatelink"
    managed_by = "terraform"
  }
}
