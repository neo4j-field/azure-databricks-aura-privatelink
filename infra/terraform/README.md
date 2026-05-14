# Terraform: NCC + Private Endpoint Rule for Aura

Provisions:

1. An account-level NCC in the specified Azure region
2. A private endpoint rule targeting the Neo4j Aura Private Link Service
3. Attachment of the NCC to a Databricks workspace

## Prerequisites

- Terraform >= 1.6.0
- Azure Databricks account admin via service principal (SP must be added to account admins)
- The Aura side has Private Link enabled and you have the PLS alias
- Service principal has `Contributor` (or finer-grained) on the Databricks workspace

## Usage

```bash
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values
terraform init
terraform plan
terraform apply
```

After apply, approve the incoming endpoint in the Aura console, then verify with `notebooks/01_validate_connectivity.py` in the parent workspace.

## Notes

- The `databricks_mws_ncc_private_endpoint_rule` resource passes `domain_names`, which is required for third-party Private Link Services (Aura, App Gateway v2). Without it, NCC-managed DNS will not route Aura's hostname to the private endpoint.
- The NCC region **must** match the workspace region. Terraform will error at apply time if they diverge.
- Rule status transitions are managed by the platform — Terraform marks the rule as created once it reaches `PENDING`. You must approve in Aura before traffic can flow.
