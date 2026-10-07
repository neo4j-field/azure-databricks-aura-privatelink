# Private Endpoint stack

Terraform for a private endpoint in your VNet into the Aura Private Link service, plus the private DNS zone, VNet link, and A record for the Aura hostname.

Use this stack when the consumer is classic Databricks, AKS, ADF, or a jump VM. For Databricks Serverless, use the [NCC stack](../databricks-ncc/) instead.

For prerequisites, usage, variables, and DNS modes, see [Private Link Terraform setup](../../../docs/setup-private-link-terraform.md). To pick between stacks, see [infra/terraform/README.md](../README.md).
