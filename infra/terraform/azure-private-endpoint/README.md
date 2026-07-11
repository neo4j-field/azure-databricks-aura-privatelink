# Terraform: Azure-Native Private Endpoint to Neo4j Aura PLS

Provisions a customer-managed Private Endpoint into Aura's Private Link Service, plus the private DNS zone wiring required for the Aura hostname to resolve to the PE NIC.

Use this stack when your consumer is **not** Databricks Serverless. Typical fits:

- Classic Azure Databricks clusters (VNet-injected workspaces)
- Azure Data Factory self-hosted IR
- AKS workloads
- Jump VMs / Bastion-fronted admin hosts
- Azure Functions on VNet integration

For **Azure Databricks Serverless**, use [`../databricks-ncc/`](../databricks-ncc/) instead. Serverless compute runs in Databricks-managed subscriptions and cannot consume a customer-VNet private endpoint.

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
cd infra/terraform/azure-private-endpoint
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
- Public access on the Aura instance should be disabled **after** validation succeeds end-to-end, not before, to avoid locking yourself out during debugging.

## Private DNS: self-managed vs. central (hub-and-spoke)

A Private Endpoint only gives you a private IP inside your subnet. The Aura hostname (`<aura-instance-id>.databases.neo4j.io`) means nothing until a **private DNS zone** answers "what IP is that hostname?" with the PE's private IP instead of Aura's public IP. This stack can own that zone for you, or step aside and let you wire it into DNS you already run. The `manage_private_dns` variable is that switch.

### `manage_private_dns = true` (default — single VNet)

Terraform creates and owns the full DNS path:

- an `azurerm_private_dns_zone` named `databases.neo4j.io`,
- an `azurerm_private_dns_zone_virtual_network_link` linking that zone to your VNet,
- an `azurerm_private_dns_a_record` mapping `<aura-instance-id>` to the PE NIC IP,
- a `private_dns_zone_group` on the PE for auto-registration.

Self-contained and correct for a single VNet with no centralized DNS. Nothing else to do after apply besides approving the endpoint in Aura.

### `manage_private_dns = false` (central DNS / hub-and-spoke)

Larger Azure estates centralize DNS: one **hub** VNet owns the private DNS zones (often fronted by Azure DNS Private Resolver), and **spoke** VNets consume them over peering and zone links. In that model the hub owns `databases.neo4j.io`, and a spoke must never create its own copy of that zone:

```
                 HUB VNet
        ┌───────────────────────────┐
        │  Azure Private DNS zones   │   <- ONE place owns databases.neo4j.io
        │  (+ optional DNS Resolver) │
        └───────────┬───────────────┘
                    │ VNet peering + zone links
        ┌───────────┼───────────────┐
        ▼           ▼               ▼
   SPOKE VNet   SPOKE VNet     SPOKE VNet
   (Databricks) (ADF)          (AKS)
        │
        ▼
   Private Endpoint -> Aura PLS   (private IP lives here)
```

If this stack tried to create a `databases.neo4j.io` zone in a spoke, one of two things breaks:

1. **Split-brain resolution** — two zones with the same name, and which one wins depends on which zone a given VNet happens to be linked to.
2. **Policy denial** — many orgs enforce Azure Policy that forbids creating private DNS zones outside the hub, so `terraform apply` fails outright.

Setting `manage_private_dns = false` makes this stack create **only the Private Endpoint**. It skips the zone, the VNet link, the A record, *and* the PE's `private_dns_zone_group` (see `main.tf`). You then own DNS. Do this in your **hub** zone:

1. **Add the A record.** In the hub's `databases.neo4j.io` zone, create `<aura-instance-id> -> <PE private IP>`. Get the IP from this stack's output:
   ```bash
   terraform output private_endpoint_nic_ip
   ```
2. **Ensure the zone is linked to every consuming VNet.** Each spoke VNet whose workloads reach Aura must be linked to the hub zone (usually already true as a standing platform pattern). With Azure DNS Private Resolver, spokes forward to the hub resolver instead of holding per-VNet links.
3. **Add the routing-host records too.** Aura VDC advertises Bolt *routing* addresses like `p-<dbid>-...neo4j.io` **after** the first connection. Those must resolve to the same PE private IP. Add each one to the hub zone as it appears, or use a wildcard/`p-*` record if your DNS policy allows it. Missing these is the classic `Cannot resolve address p-...neo4j.io:7687` failure.

Verify from any VM in a consuming VNet:
```bash
nslookup <aura-instance-id>.databases.neo4j.io   # must return the PE private IP
```
