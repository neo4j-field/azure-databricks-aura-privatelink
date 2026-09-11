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

## Definitions

Terms used throughout this README:

- **Private Endpoint (PE):** A network interface that Azure places inside one of your subnets. It holds a private IP and forwards traffic across Azure's backbone to Aura's Private Link Service, so Aura traffic stays off the public internet.
- **PE NIC:** The network interface card of that Private Endpoint. It is the object that actually owns the private IP.
- **Private IP:** The address assigned to the PE NIC from your own VNet's address range, such as `10.x.x.x`. Every reference to "the private IP" or "the PE private IP" in this README means this one address. It is what the Aura hostname must resolve to for private connectivity to work.
- **PE subnet:** The subnet that hosts the PE NIC. Its address range determines which private IP the endpoint receives.
- **PLS (Private Link Service):** The Aura-side endpoint your Private Endpoint connects to. Neo4j publishes it as an alias that you approve from the Aura console.
- **Private DNS zone:** An Azure resource that answers DNS queries for a specific domain name, but only for the VNets you link to it. It overrides public DNS for that name inside your network.
- **VNet:** An Azure Virtual Network. **Hub** and **spoke** describe a topology where one central VNet, the hub, owns shared services like DNS, and workload VNets, the spokes, connect to it over peering.

### Why `databases.neo4j.io` lives in *your* private DNS zone

`databases.neo4j.io` is a Neo4j-owned domain, so creating a private DNS zone with that name inside your subscription can look like you are taking over someone else's domain. You are not. A private DNS zone is a local override, not public ownership:

- It applies only to the VNets you explicitly link to it. Nothing outside your network sees it.
- It does not change global or public DNS, and it registers nothing with Neo4j or any domain registrar.
- Its whole purpose is to intercept the Aura hostname *inside your network* and answer with the PE's private IP. The rest of the world still resolves the same hostname to Aura's public IP.

This is the standard Azure Private Link pattern. To make access to a service private, you create a private DNS zone whose name matches that service's public hostname, so queries from your VNet land on your Private Endpoint instead of the public address. You use Neo4j's domain name precisely because your applications connect to Neo4j's hostname: the record has to match `databases.neo4j.io` for the interception to work. Your own domain would not help, because the Aura driver always connects to `<aura-instance-id>.databases.neo4j.io`.

## Notes

- The `is_manual_connection = true` flag is required for cross-subscription PLS like Aura: the consumer side cannot auto-approve the connection.
- `private_connection_resource_alias` is the canonical handle Aura publishes. Do not attempt to use an ARM resource ID for Aura's PLS. There isn't one from your side.
- Public access on the Aura instance should be disabled **after** validation succeeds end-to-end, not before, to avoid locking yourself out during debugging.

## Private DNS: self-managed vs. central (hub-and-spoke)

A Private Endpoint gives you a private IP inside your subnet, but on its own nothing connects the Aura hostname to that IP. By default `<aura-instance-id>.databases.neo4j.io` resolves to Aura's public IP. A **private DNS zone** overrides that answer inside your network so the hostname resolves to the PE's private IP instead. This stack can create and own that zone for you, or leave DNS to a system you already run. The `manage_private_dns` variable chooses between those two modes.

### `manage_private_dns = true` (default, single VNet)

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

1. **Split-brain resolution:** two zones with the same name, and which one wins depends on which zone a given VNet happens to be linked to.
2. **Policy denial:** many orgs enforce Azure Policy that forbids creating private DNS zones outside the hub, so `terraform apply` fails outright.

Setting `manage_private_dns = false` makes this stack create **only the Private Endpoint**. It skips the zone, the VNet link, the A record, *and* the PE's `private_dns_zone_group` (see `main.tf`). You then own DNS in your **hub** zone.

The steps below use the Azure CLI. Portal equivalents live under *Private DNS zones* and *DNS Private Resolver* in the portal, and the same objects map cleanly to your own Terraform or Bicep if the hub is codified. Set these shell values first, then work through the steps:

```bash
HUB_RG=<hub-resource-group>          # RG that owns the private DNS zones
ZONE=databases.neo4j.io              # the zone the hub owns
AURA_HOST=<aura-instance-id>         # e.g. abcd1234 (the label, not the FQDN)
PE_IP=$(terraform output -raw private_endpoint_nic_ip)   # from this stack, after apply
```

**Step 1: Confirm the hub owns the `databases.neo4j.io` zone.**
The platform team normally already runs this zone. Only create it if it does not exist, and only in the hub RG, never in a spoke:

```bash
az network private-dns zone show  --resource-group "$HUB_RG" --name "$ZONE" \
  || az network private-dns zone create --resource-group "$HUB_RG" --name "$ZONE"
```

**Step 2: Link the zone to every consuming VNet.**
Each spoke VNet whose workloads reach Aura must resolve against the hub zone. Add a virtual-network link per spoke with auto-registration **off**, because you are managing these records by hand:

```bash
az network private-dns link vnet create \
  --resource-group "$HUB_RG" \
  --zone-name "$ZONE" \
  --name link-<spoke-name> \
  --virtual-network <spoke-vnet-resource-id> \
  --registration-enabled false
```

If the hub fronts DNS with **Azure DNS Private Resolver**, spokes usually do not hold per-zone links at all. They point their VNet DNS at the resolver inbound endpoint, and a forwarding ruleset sends `databases.neo4j.io` to the hub. In that model this step is already handled by the standing platform pattern, and you skip straight to the A record.

**Step 3: Add the instance A record.**
Map the Aura host label to the PE NIC private IP inside the hub zone:

```bash
az network private-dns record-set a add-record \
  --resource-group "$HUB_RG" \
  --zone-name "$ZONE" \
  --record-set-name "$AURA_HOST" \
  --ipv4-address "$PE_IP"
```

**Step 4: Add the routing-host records.**
Aura VDC advertises Bolt *routing* addresses like `p-<dbid>-...neo4j.io` **after** the first connection, and those must resolve to the same PE private IP. Add each one to the hub zone as it appears:

```bash
az network private-dns record-set a add-record \
  --resource-group "$HUB_RG" \
  --zone-name "$ZONE" \
  --record-set-name "p-<dbid>-0" \
  --ipv4-address "$PE_IP"
```

If your DNS policy permits wildcards, a single `p-*` record set covers all of them and saves chasing hostnames one at a time. Missing these records is the classic `Cannot resolve address p-...neo4j.io:7687` failure.

**Step 5: Verify from any VM in a consuming VNet.**
Resolution must return the PE private IP, not Aura's public address:

```bash
nslookup <aura-instance-id>.databases.neo4j.io   # must return the PE private IP
```

If it still returns a public IP, the VNet is not linked to the hub zone (or the resolver forwarding rule is missing), so revisit step 2.
