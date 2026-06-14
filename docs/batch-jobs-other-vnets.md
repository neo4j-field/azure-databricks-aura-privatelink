# Batch Jobs and Workloads in Other VNets

When public access is disabled on Aura, any workload running in a VNet that is not
linked to the Private DNS Zone for `databases.neo4j.io` will resolve the Aura
hostname via public DNS, receive the public IP, and be refused.

This guide covers restoring connectivity for Azure workloads in other VNets —
including classic Databricks clusters, Azure Data Factory, Azure Kubernetes Service,
Azure Machine Learning compute clusters, Azure Functions, and any VM-based batch
process.

---

## Why it breaks

```
Batch VNet                                Consumer VNet
──────────────────────────────────────    ──────────────────────────────────────
  Batch VM / AKS node / ADF IR              Your app VMs
    │                                          │
    │ DNS: <dbid>.databases.neo4j.io          │ DNS: <dbid>.databases.neo4j.io
    │ → public: 52.x.x.x ✗ refused            │ → Private DNS Zone → 10.x.x.x ✓
```

The Private DNS Zone `databases.neo4j.io` is linked to the consumer VNet only.
Workloads in any other VNet resolve via public DNS until you explicitly link the
zone to their VNet.

---

## Which fix to choose

| Scenario | Fix |
|----------|-----|
| Batch VNet is in the **same subscription** as the consumer VNet | [Option A](#option-a-add-a-vnet-link-to-the-private-dns-zone) — add a VNet link and VNet peering |
| Batch VNet is in a **different subscription** | [Option B](#option-b-create-a-new-private-endpoint-in-the-batch-vnet) — add subscription to Aura, deploy a new Private Endpoint |
| **Databricks Serverless** | Use the [`databricks-ncc/` Terraform stack](../infra/terraform/databricks-ncc/) — NCC manages DNS independently |
| **Databricks classic (VNet-injected)** | Option A — link the DNS zone to the Databricks VNet + peer VNets |

---

## Option A: Add a VNet link to the Private DNS Zone

A single Private Endpoint NIC is reachable from any peered VNet — Azure exports
the endpoint NIC's subnet route across VNet peerings. The only missing piece is
usually DNS: the Private DNS Zone must be explicitly linked to each VNet whose
workloads need to resolve the Aura hostname.

### Step 1: Peer the batch VNet to the consumer VNet

If the batch VNet is not already peered to the consumer VNet:

```bash
# Consumer → Batch direction
az network vnet peering create \
  --name consumer-to-batch \
  --resource-group <consumer-rg> \
  --vnet-name <consumer-vnet> \
  --remote-vnet /subscriptions/<subscription-id>/resourceGroups/<batch-rg>/providers/Microsoft.Network/virtualNetworks/<batch-vnet> \
  --allow-vnet-access \
  --allow-forwarded-traffic

# Batch → Consumer direction (peering must be created both ways)
az network vnet peering create \
  --name batch-to-consumer \
  --resource-group <batch-rg> \
  --vnet-name <batch-vnet> \
  --remote-vnet /subscriptions/<subscription-id>/resourceGroups/<consumer-rg>/providers/Microsoft.Network/virtualNetworks/<consumer-vnet> \
  --allow-vnet-access \
  --allow-forwarded-traffic
```

Or with Terraform:

```hcl
resource "azurerm_virtual_network_peering" "consumer_to_batch" {
  name                      = "consumer-to-batch"
  resource_group_name       = var.consumer_resource_group
  virtual_network_name      = var.consumer_vnet_name
  remote_virtual_network_id = data.azurerm_virtual_network.batch.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
}

resource "azurerm_virtual_network_peering" "batch_to_consumer" {
  name                      = "batch-to-consumer"
  resource_group_name       = var.batch_resource_group
  virtual_network_name      = var.batch_vnet_name
  remote_virtual_network_id = data.azurerm_virtual_network.consumer.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
}
```

### Step 2: Add a VNet link to the Private DNS Zone

```bash
az network private-dns link vnet create \
  --resource-group <consumer-rg> \
  --zone-name "databases.neo4j.io" \
  --name "link-to-batch-vnet" \
  --virtual-network /subscriptions/<subscription-id>/resourceGroups/<batch-rg>/providers/Microsoft.Network/virtualNetworks/<batch-vnet> \
  --registration-enabled false
```

Or with Terraform:

```hcl
data "azurerm_virtual_network" "batch" {
  name                = var.batch_vnet_name
  resource_group_name = var.batch_resource_group
}

resource "azurerm_private_dns_zone_virtual_network_link" "batch" {
  name                  = "link-to-batch-vnet"
  resource_group_name   = var.consumer_resource_group
  private_dns_zone_name = "databases.neo4j.io"
  virtual_network_id    = data.azurerm_virtual_network.batch.id
  registration_enabled  = false
}
```

### Step 3: Validate DNS from the batch VNet

SSH or Bastion into any VM in the batch VNet and run:

```bash
nslookup <dbid>.databases.neo4j.io
```

The response must be the Private Endpoint NIC IP (a `10.x` address). If you see
a public IP, the VNet link was not applied to the correct zone or the peering is
not yet fully propagated (allow 2–5 minutes).

### Step 4: Update the connection URI

The Aura credentials file uses the public hostname. Update batch job configuration
to use the Private URI from the Aura console:

```
# Before (public, now refused)
NEO4J_URI=neo4j+s://b7253d3b.databases.neo4j.io

# After (private — same hostname, routed via Private Endpoint)
NEO4J_URI=neo4j+s://b7253d3b.databases.neo4j.io
```

> On Azure, the Private URI and the public URI share the same hostname
> (`<dbid>.databases.neo4j.io`). Only the DNS resolution changes — the private
> zone resolves it to the PE NIC IP. No hostname change in your connection string
> is required, unlike other cloud providers.

---

## Option B: Create a new Private Endpoint in the batch VNet

When the batch VNet is in a different Azure subscription, VNet linking alone is
insufficient — you must create a new Private Endpoint in that subscription. A
Private Link Service allows multiple consumers from different subscriptions.

### Step 1: Register the batch subscription in Aura

Open the Aura console → **Security → Network Access → Network Access** →
edit the network access configuration and add the batch subscription ID to the
**Target Azure Subscription IDs** list. This allowlists incoming connection
requests from the batch subscription.

### Step 2: Deploy a new Private Endpoint in the batch VNet

Use the [`azure-private-endpoint/` Terraform stack](../infra/terraform/azure-private-endpoint/)
with a `terraform.tfvars` file targeting the batch subscription:

```hcl
azure_subscription_id = "<batch-subscription-id>"
azure_tenant_id       = "<tenant-id>"
resource_group_name   = "<batch-resource-group>"
virtual_network_name  = "<batch-vnet>"
pe_subnet_name        = "<batch-pe-subnet>"
private_endpoint_name = "pe-neo4j-aura-batch"
aura_pls_alias        = "<pls-alias-from-aura-console>"  # same PLS alias as before
aura_instance_id      = "<dbid>"
manage_private_dns    = true
```

```bash
cd infra/terraform/azure-private-endpoint
terraform init
terraform apply -var-file="batch.tfvars"
```

### Step 3: Approve the connection in the Aura console

The new Private Endpoint creates a pending connection request in Aura.

1. Aura console → **Security → Network Access**
2. Locate the pending request from the batch subscription
3. Click **Accept**
4. Wait for status to reach **Approved**

Validate DNS from the batch VNet:

```bash
nslookup <dbid>.databases.neo4j.io
```

Should return the new PE NIC IP in the batch VNet.

---

## Workload-specific configuration

### Classic Azure Databricks (VNet-injected)

Databricks VNet-injected clusters run in a customer-managed VNet. Apply Option A
(DNS zone VNet link + VNet peering to the consumer VNet). Verify from a cluster
notebook:

```python
import socket
print(socket.gethostbyname("<dbid>.databases.neo4j.io"))  # expect 10.x.x.x
```

### Azure Data Factory — Self-hosted Integration Runtime

The self-hosted IR is a VM (or VM Scale Set) in your VNet. Apply Option A for the
IR's VNet. Update the ADF linked service connection string to the Private URI.

For an **Azure-hosted IR**, network access goes through Microsoft-managed
infrastructure that cannot be VNet-peered. Use a **self-hosted IR** instead, or
route via the Managed VNet IR if ADF Managed Virtual Network is enabled in your
workspace.

### Azure Kubernetes Service (AKS)

AKS nodes run in the AKS node VNet. Apply Option A for the node VNet. Confirm
CoreDNS resolves correctly from inside a pod:

```bash
kubectl run dns-test --image=busybox --restart=Never --rm -it -- \
  nslookup <dbid>.databases.neo4j.io
```

Expect the PE NIC IP. If CoreDNS returns the public IP, the node VNet's DNS
settings may be using a custom DNS server that is not forwarding to Azure DNS
(168.63.129.16). Check the AKS cluster's DNS configuration.

### Azure Machine Learning Compute Clusters

ML compute clusters in a managed or customer VNet need the DNS zone linked to
their VNet. For **AzureML Managed VNets**, add an outbound private endpoint rule
in the AzureML workspace Network settings targeting the Aura PLS alias directly
(similar to the Databricks NCC approach).

### Azure Functions and App Service (VNet Integration)

Functions connected via **VNet Integration** route outbound traffic through your
VNet. Apply Option A for the integrated VNet and enable **Route All** under VNet
Integration settings to force all egress (including DNS) through the VNet:

```bash
az functionapp vnet-integration add \
  --resource-group <rg> \
  --name <function-app-name> \
  --vnet <batch-vnet-name> \
  --subnet <integration-subnet>

az resource update \
  --resource-group <rg> \
  --name <function-app-name>/config/web \
  --resource-type Microsoft.Web/sites \
  --set properties.vnetRouteAllEnabled=true
```

### Azure Batch

Azure Batch pools deployed in a VNet subnet use the VNet's DNS. Apply Option A
for the Batch pool VNet, then reference the Private URI in the pool's task
environment variables.

---

## Multiple Aura instances in the same VNet

Each Aura instance has its own `<dbid>` A record. The Private DNS Zone
`databases.neo4j.io` is a **shared zone** — you add one record per instance, not
one zone per instance. When onboarding a second Aura instance, run the
`azure-private-endpoint` Terraform stack for the new instance with `manage_private_dns = false`
and add the A record manually, or set `aura_instance_id` to the new instance ID
and Terraform adds a second A record in the same zone.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `nslookup` returns public IP from batch VM | DNS zone not linked to batch VNet | Add VNet link (Option A Step 2) |
| VNet link exists but `nslookup` still returns public IP | Peering not established; DNS traffic not reaching the zone | Confirm peering is in **Connected** state; also check that the batch VM's DNS is Azure DNS (168.63.129.16) |
| PE connection request stuck in **Pending** in Aura | Batch subscription not in Aura's subscription allowlist | Add batch subscription ID in Aura Network Access console (Option B Step 1) |
| AKS CoreDNS returns public IP despite VNet link | AKS uses custom DNS server not forwarding to Azure DNS | Add a conditional forwarder for `databases.neo4j.io` to `168.63.129.16` in the custom DNS server |
| Databricks NCC rule stays **PENDING** | Wrong subscription ID in Aura NCC managed subscription list | Remove and re-add the NCC workspace binding with the correct Databricks-managed subscription ID |
| Connection refused from ADF Azure IR | Azure IR cannot be peered to customer VNet | Switch to self-hosted IR in the batch VNet |
