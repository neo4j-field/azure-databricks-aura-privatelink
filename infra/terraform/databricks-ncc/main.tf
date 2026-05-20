terraform {
  required_version = ">= 1.6.0"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = ">= 1.55.0"
    }
  }
}

# Account-level provider — for NCC, private endpoint rule, and NCC binding.
# Authenticate with the Azure Databricks account via service principal that is
# an account admin. Account-level Terraform must target the accounts host.
provider "databricks" {
  alias               = "account"
  host                = "https://accounts.azuredatabricks.net"
  account_id          = var.databricks_account_id
  azure_client_id     = var.azure_client_id
  azure_client_secret = var.azure_client_secret
  azure_tenant_id     = var.azure_tenant_id
}

# ----------------------------------------------------------------------------
# 1. Network Connectivity Configuration (account-level, region-scoped)
# ----------------------------------------------------------------------------
resource "databricks_mws_network_connectivity_config" "ncc" {
  provider = databricks.account
  name     = var.ncc_name
  region   = var.azure_region
}

# ----------------------------------------------------------------------------
# 2. Private Endpoint Rule for the Neo4j Aura PLS
#
# The `domain_names` field is required for third-party Private Link Services
# (Aura, App Gateway v2, etc.). Without it, NCC-managed DNS will not route the
# Aura hostname to the private endpoint and traffic will fall back to public.
# ----------------------------------------------------------------------------
resource "databricks_mws_ncc_private_endpoint_rule" "aura" {
  provider                       = databricks.account
  network_connectivity_config_id = databricks_mws_network_connectivity_config.ncc.network_connectivity_config_id

  # For third-party Private Link Services (Neo4j Aura, Azure App Gateway v2, etc.)
  # the provider expects `resource_id` (the PLS alias) plus `domain_names`. The
  # `group_id` field is reserved for first-party Azure resources and is mutually
  # exclusive with `domain_names`.
  resource_id  = var.aura_pls_resource_id
  domain_names = distinct(concat([var.aura_private_hostname], var.aura_extra_domain_names))
}

# ----------------------------------------------------------------------------
# 3. Bind the NCC to the target workspace.
#
# `databricks_mws_ncc_binding` is the supported resource for attaching an NCC
# to an existing Azure Databricks workspace without touching unrelated workspace
# attributes (pricing tier, managed RG, etc.). Use it instead of
# `databricks_mws_workspaces`, which is intended for full workspace lifecycle
# management and is fragile when applied to a workspace created out-of-band.
# ----------------------------------------------------------------------------
resource "databricks_mws_ncc_binding" "this" {
  provider                       = databricks.account
  network_connectivity_config_id = databricks_mws_network_connectivity_config.ncc.network_connectivity_config_id
  workspace_id                   = var.databricks_workspace_id
}

# ----------------------------------------------------------------------------
# 4. Outputs
# ----------------------------------------------------------------------------
output "ncc_id" {
  value       = databricks_mws_network_connectivity_config.ncc.network_connectivity_config_id
  description = "ID of the created NCC. Use this in API calls and console URLs."
}

output "ncc_name" {
  value = databricks_mws_network_connectivity_config.ncc.name
}

output "private_endpoint_rule_id" {
  value       = databricks_mws_ncc_private_endpoint_rule.aura.rule_id
  description = "ID of the Aura private endpoint rule. Status transitions through PENDING -> ESTABLISHED after Aura-side approval."
}

output "next_steps" {
  value = <<-EOT
    NCC, private endpoint rule, and workspace binding provisioned.

    Next:
    1. Approve the incoming endpoint request in the Aura console
       (Security -> Network Access -> Pending approvals).
    2. Wait until the rule reads ESTABLISHED in the Databricks NCC view
       (refresh the account console; can take a few minutes).
    3. Restart any running serverless compute (SQL warehouses, running jobs).
    4. Run notebooks/03_dbxuk_svrless_drose_smoke_test.py from your workspace
       to validate the private path end-to-end.
  EOT
}
