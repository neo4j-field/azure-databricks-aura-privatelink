variable "databricks_account_id" {
  description = "Azure Databricks account ID (from accounts.azuredatabricks.net)"
  type        = string
}

variable "databricks_workspace_id" {
  description = "Azure Databricks workspace ID to attach the NCC to"
  type        = string
}

variable "databricks_workspace_url" {
  description = "Azure Databricks workspace URL (https://adb-xxx.xx.azuredatabricks.net)"
  type        = string
}

variable "azure_client_id" {
  description = "Service principal client ID with account-admin role"
  type        = string
  sensitive   = true
}

variable "azure_client_secret" {
  description = "Service principal client secret"
  type        = string
  sensitive   = true
}

variable "azure_tenant_id" {
  description = "Azure AD tenant ID"
  type        = string
}

variable "azure_region" {
  description = "Azure region for NCC (must match workspace region)"
  type        = string
  default     = "eastus"
}

variable "ncc_name" {
  description = "Name for the Network Connectivity Configuration"
  type        = string
  default     = "ncc-aura-privatelink"
}

variable "aura_pls_resource_id" {
  description = "Neo4j Aura Private Link Service alias (from Aura console after enabling Private Link)"
  type        = string
}

variable "aura_group_id" {
  description = "Group identifier for the Aura PLS (provided by Aura)"
  type        = string
}

variable "aura_private_hostname" {
  description = "Neo4j Aura Private URI hostname, e.g. d48d6199.databases.neo4j.io"
  type        = string
}
