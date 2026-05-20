# Terraform: Databricks NCC + Aura PLS Private Endpoint Rule

Provisions, end-to-end on the Databricks side:

1. An account-level **Network Connectivity Configuration** (NCC) in the target Azure region
2. A **private endpoint rule** under the NCC targeting the Neo4j Aura Private Link Service, with `domain_names` set so NCC-managed DNS routes the Aura hostname privately, including optional Neo4j routing hostnames
3. An **NCC binding** that attaches the NCC to your existing Azure Databricks workspace

Use this stack when your consumer is **Azure Databricks Serverless**. For classic clusters, AKS, ADF, jump VMs, etc., use the sibling stack [`../azure-private-endpoint/`](../azure-private-endpoint/).

## Prerequisites

- Terraform >= 1.6.0
- Databricks provider >= 1.55.0 (for `databricks_mws_ncc_binding`)
- An identity that is an **Azure Databricks account admin**. Two supported paths:
  - **Azure CLI auth (default in this stack)**: be signed in via `az login` as a user that's in the Account Admins group at `accounts.azuredatabricks.net → User management → Admins`. Leave `azure_client_id` / `azure_client_secret` / `azure_tenant_id` unset.
  - **Service principal (for CI/CD)**: create an SP, grant it account-admin in the Databricks Account Console, fill the three `azure_*` variables.
- The Aura side has Private Link enabled and the right subscriptions are registered in its Network Access config (see *Third-party PLS visibility* below)
- The target Databricks workspace already exists; this stack only attaches the NCC to it

## Usage

```bash
cd infra/terraform/databricks-ncc
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars

az login --tenant <YOUR_AZURE_AD_TENANT_ID>
az account set --subscription <YOUR_SUBSCRIPTION_ID>

terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

After a successful apply:

1. Approve the incoming PE in the Aura console (*Security → Network Access → Pending approvals*).
2. Refresh the NCC view in the Databricks account console — the rule status should transition from `PENDING` to `ESTABLISHED` (usually under a minute).
3. Restart any running serverless compute in the workspace (SQL warehouses, jobs).
4. Run a smoke-test notebook from the workspace to validate the private path.

## Third-party PLS visibility (must-read for Aura)

When the NCC creates the PE on Databricks' side, the request originates from a **Databricks-managed Azure subscription** for the workspace's region — not from your subscription. Aura's PLS enforces a visibility allow-list ("Target Azure Subscription IDs" in the Aura Network Access wizard), and if the Databricks-managed sub isn't on that list, Azure rejects PE creation with:

```
ThirdPartyPrivateLinkServiceProvidedDuringPrivateEndpointCreationDoesNotExistOrIsNotVisible
```

The failed `terraform apply` exposes the right sub ID in its error message — look for a path like `/subscriptions/<guid>/resourceGroups/prod-<region>-snp-...`. That `<guid>` is the Databricks-managed sub for that region.

**Fix:**

1. Open the Aura console → **Security → Network Access** → edit the configuration for the Aura instance's region.
2. **Add subscription ID** → paste the Databricks-managed sub from the error path. Keep your existing subs in the list.
3. Save and wait ~1 minute.
4. Re-run `terraform plan -out=tfplan && terraform apply tfplan`.

Databricks may retry PE creation from more than one managed subscription in a region. If a retry exposes a different `/subscriptions/<guid>/resourceGroups/prod-<region>-snp-...` value, add that subscription ID to Aura too, then retry.

This step isn't called out explicitly in either Neo4j Aura or Microsoft Databricks public documentation as of the last verification of this repo. It is a known operational gotcha for NCC + third-party PLS.

## Neo4j routing hostnames

Aura VDC can advertise Bolt routing addresses such as `p-<dbid>-....neo4j.io` after the initial connection to `<dbid>.databases.neo4j.io`. These hostnames must also resolve through NCC-managed DNS. If the validation notebook fails with:

```
ValueError: Cannot resolve address p-...neo4j.io:7687
```

add the hostname to `aura_extra_domain_names` and re-apply:

```hcl
aura_extra_domain_names = [
  "p-b7253d3b-944d-0005.production-orch-0477.neo4j.io",
]
```

Terraform updates the existing private endpoint rule in place by adding the hostname to `domain_names`.

## Why `databricks_mws_ncc_binding` and not `databricks_mws_workspaces`

`databricks_mws_workspaces` is intended for full workspace lifecycle management and is fragile when applied to a workspace that was created out-of-band — it tries to reconcile every workspace attribute it knows about, producing spurious diffs and risking unintended workspace changes. `databricks_mws_ncc_binding` is a narrow, purpose-built resource that only manages the NCC-to-workspace association.

## Notes

- The `domain_names` field on `databricks_mws_ncc_private_endpoint_rule` is mandatory for third-party Private Link Services. Without it, NCC-managed DNS will not route the Aura hostname to the private endpoint and traffic falls back to the public IP. Include any `p-*.neo4j.io` routing hostnames returned by the driver. The `group_id` field is mutually exclusive with `domain_names` in current provider versions and must not be set for third-party PLS.
- NCC region must match the Databricks workspace region exactly. The *target* PLS region can differ (cross-region PLS is supported), but you pay inter-region latency and bandwidth.
- A rule left in `PENDING` for 14 days expires. Recreate via `terraform taint databricks_mws_ncc_private_endpoint_rule.aura && terraform apply` if that happens.
