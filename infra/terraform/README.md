# Terraform: Databricks NCC + Aura PLS Private Endpoint Rule

Provisions, end-to-end on the Databricks side:

1. An account-level **Network Connectivity Configuration** (NCC) in the target Azure region
2. A **private endpoint rule** under the NCC targeting the Neo4j Aura Private Link Service, with `domain_names` set so NCC-managed DNS routes the Aura hostname privately
3. An **NCC binding** that attaches the NCC to your existing Azure Databricks workspace

Use this stack when your consumer is **Azure Databricks Serverless**. For classic clusters, AKS, ADF, etc., use the sibling stack in [`azure-native-pe/`](azure-native-pe/).

## Prerequisites

- Terraform >= 1.6.0
- Databricks provider >= 1.55.0 (for `databricks_mws_ncc_binding`)
- A service principal that is:
  - **Account admin** in the Azure Databricks account (granted under *Account Console -> User management -> Service principals*)
  - Has rights to reach the Databricks account API (no Azure Contributor required for the NCC operations themselves)
- The Aura side has Private Link enabled and the consumer subscription is registered in Aura's Network Access config
- The Databricks workspace already exists; this stack only attaches the NCC to it

## Usage

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars
terraform init
terraform plan
terraform apply
```

After apply:

1. Approve the incoming PE in the Aura console (*Security -> Network Access -> Pending approvals*)
2. Refresh the NCC view in the Databricks account console — the rule status should transition to `ESTABLISHED`
3. Restart any running serverless compute in the workspace
4. Run `notebooks/03_dbxuk_svrless_drose_smoke_test.py` (or your own validation notebook) to verify the private path

## Why `databricks_mws_ncc_binding` and not `databricks_mws_workspaces`

`databricks_mws_workspaces` is intended for full workspace lifecycle management and is fragile when applied to a workspace that was created out-of-band (it will try to reconcile every workspace attribute it knows about, often producing spurious diffs and risking workspace-level changes you did not intend). `databricks_mws_ncc_binding` is a narrow, purpose-built resource that only manages the NCC-to-workspace association — exactly what we need here.

## Notes

- The `domain_names` field on `databricks_mws_ncc_private_endpoint_rule` is mandatory for third-party Private Link Services like Neo4j Aura or Azure App Gateway v2. Without it, NCC-managed DNS will not route the Aura hostname to the private endpoint and traffic will fall back to the public IP.
- NCC region must match the Databricks workspace region exactly. Terraform will surface an error at apply time if they diverge.
- The rule transitions through `PENDING` -> `ESTABLISHED` after the Aura admin approves the connection. A rule left in `PENDING` for 14 days expires; recreate via `terraform taint databricks_mws_ncc_private_endpoint_rule.aura && terraform apply` if that happens.
