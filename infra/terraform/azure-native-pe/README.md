# Terraform: Azure-Native Private Endpoint to Neo4j Aura PLS

Provisions a customer-managed Private Endpoint into Aura's Private Link Service, plus the private DNS zone wiring required for the Aura hostname to resolve to the PE NIC.

Use this stack when your consumer is **not** Databricks Serverless. Typical fits:

- Classic Azure Databricks clusters (VNet-injected workspaces)
- Azure Data Factory self-hosted IR
- AKS workloads
- Jump VMs / Bastion-fronted admin hosts
- Azure Functions on VNet integration

For **Azure Databricks Serverless**, use the NCC stack in [`../`](..) instead. Serverless compute runs in Databricks-managed subscriptions and cannot consume a customer-VNet private endpoint.

## What this stack creates

| Resource | Purpose |
|---|---|
| `azurerm_private_endpoint` | The PE NIC inside your subnet that connects to Aura's PLS via alias |
| `azurerm_private_dns_zone` (`databases.neo4j.io`) | Private DNS zone for the Aura hostname (optional, see `manage_private_dns`) |
| `azurerm_private_dns_zone_virtual_network_link` | Links the zone to your VNet so resolvers in that VNet see it |
| `azurerm_private_dns_a_record` | Maps `<aura-instance-id>.databases.neo4j.io` to the PE NIC IP |

## Prerequisites

- Terraform >= 1.6.0
- An existing VNet and subnet that will host the PE NIC. The subnet must allow private endpoints (default for modern subnets).
- The subscription you are deploying into is **already registered** in the Aura console under *Network Access -> Target Azure Subscription IDs*. Without this, the PLS connection request will not appear in Aura for approval.
- Azure credentials (CLI login, environment variables, or managed identity) with permission to create PEs and private DNS zones in the target RG.

## Usage

```bash
cd infra/terraform/azure-native-pe
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars
terraform init
terraform plan
terraform apply
```

After apply:

1. Approve the incoming endpoint in the Aura console (*Security -> Network Access -> Pending approvals*).
2. Verify resolution from any VM in the linked VNet:
   ```bash
   nslookup <aura-instance-id>.databases.neo4j.io
   ```
   The answer must be a private IP from the PE subnet.
3. Open a Bolt connection (`neo4j+s://<aura-instance-id>.databases.neo4j.io`) to confirm end-to-end traffic.

## Notes

- The `is_manual_connection = true` flag is required for cross-subscription PLS like Aura — the consumer side cannot auto-approve the connection.
- `private_connection_resource_alias` is the canonical handle Aura publishes. Do not attempt to use an ARM resource ID for Aura's PLS — there isn't one from your side.
- If you manage `databases.neo4j.io` DNS centrally (e.g., via Azure Private DNS Resolver hub-and-spoke), set `manage_private_dns = false` and create the zone link and A record in your central zone instead.
- Public access on the Aura instance should be disabled **after** validation succeeds end-to-end, not before, to avoid locking yourself out during debugging.
