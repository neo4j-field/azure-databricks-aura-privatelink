terraform {
  required_version = ">= 1.6.0"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = ">= 1.50.0"
    }
  }
}

# Account-level provider — for NCC + private endpoint rule provisioning.
# Authenticate with DATABRICKS_HOST (https://accounts.azuredatabricks.net)
# and a service principal that is an Azure Databricks account admin.
provider "databricks" {
  alias              = "account"
  host               = "https://accounts.azuredatabricks.net"
  account_id         = var.databricks_account_id
  azure_client_id    = var.azure_client_id
  azure_client_secret = var.azure_client_secret
  azure_tenant_id    = var.azure_tenant_id
}

# Workspace-level provider — for attaching the NCC and managing secret scopes.
provider "databricks" {
  alias = "workspace"
  host  = var.databricks_workspace_url
}

# ----------------------------------------------------------------------------
# 1. Network Connectivity Configuration (account-level)
# ----------------------------------------------------------------------------
resource "databricks_mws_network_connectivity_config" "ncc" {
  provider = databricks.account
  name     = var.ncc_name
  region   = var.azure_region
}

# ----------------------------------------------------------------------------
# 2. Private Endpoint Rule for Neo4j Aura PLS
#
# Uses the Network Connectivity API path that supports `domain_names`,
# required for third-party Private Link Services (Aura, App Gateway v2, etc.).
# ----------------------------------------------------------------------------
resource "databricks_mws_ncc_private_endpoint_rule" "aura" {
  provider                    = databricks.account
  network_connectivity_config_id = databricks_mws_network_connectivity_config.ncc.network_connectivity_config_id

  resource_id  = var.aura_pls_resource_id   # PLS alias from Aura console
  group_id     = var.aura_group_id          # group identifier from Aura
  domain_names = [var.aura_private_hostname] # e.g., "d48d6199.databases.neo4j.io"
}

# ----------------------------------------------------------------------------
# 3. Attach NCC to Workspace
# ----------------------------------------------------------------------------
data "databricks_mws_workspaces" "all" {
  provider = databricks.account
}

resource "databricks_mws_workspaces" "workspace_ncc_attach" {
  provider                              = databricks.account
  account_id                            = var.databricks_account_id
  workspace_id                          = var.databricks_workspace_id
  network_connectivity_config_id        = databricks_mws_network_connectivity_config.ncc.network_connectivity_config_id

  lifecycle {
    ignore_changes = [
      # Avoid clobbering unrelated workspace fields managed elsewhere.
      workspace_name,
      pricing_tier,
    ]
  }
}

# ----------------------------------------------------------------------------
# 4. Outputs
# ----------------------------------------------------------------------------
output "ncc_id" {
  value       = databricks_mws_network_connectivity_config.ncc.network_connectivity_config_id
  description = "ID of the created NCC (use this in API calls and console)"
}

output "ncc_name" {
  value = databricks_mws_network_connectivity_config.ncc.name
}

output "private_endpoint_rule_id" {
  value       = databricks_mws_ncc_private_endpoint_rule.aura.rule_id
  description = "ID of the Aura private endpoint rule"
}

output "next_steps" {
  value = <<-EOT
    NCC and private endpoint rule provisioned.

    Next:
    1. Approve the incoming endpoint request in Aura console
       (Security -> Network Access -> Pending approvals)
    2. Wait until rule status reads ESTABLISHED in Databricks NCC view
    3. Restart any running serverless compute resources
    4. Run notebooks/01_validate_connectivity.py to verify the path
  EOT
}
