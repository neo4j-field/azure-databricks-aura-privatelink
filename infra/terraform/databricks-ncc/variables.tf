variable "databricks_account_id" {
  description = "Azure Databricks account ID (from accounts.azuredatabricks.net)"
  type        = string
}

variable "databricks_workspace_id" {
  description = "Azure Databricks workspace ID to attach the NCC to"
  type        = string
}

variable "databricks_workspace_url" {
  description = "Azure Databricks workspace URL. Not consumed by this stack; kept for documentation and future workspace-level provider use (e.g., secret scope automation)."
  type        = string
  default     = null
  nullable    = true
}

variable "azure_client_id" {
  description = "Service principal client ID with Databricks account-admin role. Leave null to fall back to Azure CLI auth (`az login` as a user that is a Databricks account admin)."
  type        = string
  sensitive   = true
  default     = null
  nullable    = true
}

variable "azure_client_secret" {
  description = "Service principal client secret. Leave null to fall back to Azure CLI auth."
  type        = string
  sensitive   = true
  default     = null
  nullable    = true
}

variable "azure_tenant_id" {
  description = "Azure AD tenant ID. Leave null to let the provider read it from the Azure CLI context."
  type        = string
  default     = null
  nullable    = true
}

variable "azure_region" {
  description = "Azure region for the NCC. Must match the Databricks workspace region exactly. Example: uksouth"
  type        = string
}

variable "ncc_name" {
  description = "Name for the Network Connectivity Configuration (lowercase, hyphen-delimited)."
  type        = string
  default     = "ncc-aura-privatelink"
}

variable "aura_pls_resource_id" {
  description = "Neo4j Aura Private Link Service alias (from Aura console after enabling Private Link)"
  type        = string
}

variable "aura_private_hostname" {
  description = "Neo4j Aura Private URI hostname, e.g. b7253d3b.databases.neo4j.io. Used by NCC-managed DNS to route the hostname to the private endpoint."
  type        = string
}

variable "aura_extra_domain_names" {
  description = "Additional Neo4j hostnames returned by the driver routing table that must resolve through NCC-managed DNS."
  type        = list(string)
  default     = []
}
