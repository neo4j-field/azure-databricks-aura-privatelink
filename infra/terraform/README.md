# Terraform stacks

Two independent Terraform stacks, the **NCC stack** and the **Private Endpoint stack**, picked by **which compute is reaching Aura**:

| Stack | Use when your consumer is | What it creates |
|---|---|---|
| **NCC stack**: [`databricks-ncc/`](databricks-ncc/). Setup guide: [NCC Terraform setup](../../docs/setup-ncc-terraform.md) | **Azure Databricks Serverless** | Databricks NCC + private endpoint rule + workspace binding. The PE itself lives in the Databricks-managed subscription. Nothing is created in your subscription. |
| **Private Endpoint stack**: [`azure-private-endpoint/`](azure-private-endpoint/). Setup guide: [Private Link Terraform setup](../../docs/setup-private-link-terraform.md) | Classic Databricks (VNet-injected), AKS, ADF self-hosted IR, jump VMs, Functions on VNet integration | An `azurerm_private_endpoint` in your VNet + a `databases.neo4j.io` private DNS zone + VNet link + A record. |

The two stacks are independent root modules. You can apply only one, only the other, or both side by side if different consumer classes in the same subscription need to reach Aura over both surfaces.

## Decision flow

![Decision flow for choosing the NCC stack, the Private Endpoint stack, or both](../../docs/images/terraform-decision-flow.svg)

## NCC stack: Aura subscription allow-list

The first NCC apply fails until Aura allow-lists the Databricks-managed subscription that sends the request. See [Aura console Step 3](../../docs/shared/aura-console-steps.md#step-3-allow-list-the-consumer-subscription).
