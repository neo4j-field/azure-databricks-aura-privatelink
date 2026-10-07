# Private DNS for Private Link

A private endpoint gives you a private IP inside your subnet. On its own, nothing connects the Aura hostname to that IP. By default `<aura-instance-id>.databases.neo4j.io` resolves to the Aura public IP. A private DNS zone overrides that answer inside your network, so the hostname resolves to the endpoint private IP instead.

Both Private Link guides use this page: [Private Link manual setup](../setup-private-link-manual.md) and [Private Link Terraform setup](../setup-private-link-terraform.md).

## Why databases.neo4j.io lives in your private DNS zone

`databases.neo4j.io` is a domain that Neo4j owns. Creating a private DNS zone with that name in your subscription can look like taking over someone else's domain. It is a local override, not public ownership:

- The zone applies only to the VNets you link to it. Nothing outside your network sees it.
- The zone does not change public DNS. It registers nothing with Neo4j or with any domain registrar.
- The zone answers the Aura hostname with the endpoint private IP, and only inside your network. The rest of the world still resolves the same hostname to the Aura public IP.

This is the standard Azure Private Link pattern. To make access to a service private, you create a private DNS zone whose name matches the public hostname of that service. Queries from your VNet then land on your private endpoint instead of the public address.

The zone must use the Neo4j domain name because your applications connect to the Neo4j hostname. The Aura driver always connects to `<aura-instance-id>.databases.neo4j.io`. A zone under your own domain would never match that name.

## Self-managed vs. central DNS

You can own the zone yourself next to the endpoint, or leave it to a central hub that your platform team already runs.

**Self-managed, single VNet:** The zone lives in the same resource group as the endpoint and links to your VNet. The script, Steps 5 and 6 of the [manual guide](../setup-private-link-manual.md#step-5-create-the-private-dns-zone-and-link-it), and the Terraform stack with `manage_private_dns = true` all build this mode. It suits a single VNet with no central DNS.

**Central hub DNS:** Larger Azure estates centralize DNS. One hub VNet owns the private DNS zones, and Azure DNS Private Resolver often fronts them. Spoke VNets consume the zones over peering and zone links. In that model the hub owns `databases.neo4j.io`, and a spoke must never create its own copy of the zone.

![A hub VNet owns the private DNS zones and spoke VNets consume them through peering and zone links](../images/hub-and-spoke-dns.svg)

A second `databases.neo4j.io` zone in a spoke breaks one of two ways:

1. **Split-brain resolution:** Two zones share the same name. The answer a VNet gets depends on which zone that VNet is linked to.
2. **Policy denial:** Many organizations use Azure Policy to forbid private DNS zones outside the hub. In that case the zone create fails outright.

For central hub DNS, create only the private endpoint on the consumer side:

- **Manual commands:** Run Steps 1 to 4 of the [manual guide](../setup-private-link-manual.md) and skip Steps 5 and 6.
- **Script:** The script supports the single-VNet mode only. Use the manual commands instead.
- **Terraform:** Set `manage_private_dns = false`. The stack then creates only the private endpoint. It skips the zone, the VNet link, the A record, and the `private_dns_zone_group` on the endpoint.

Then wire DNS in the hub as in [Set up central hub DNS](#set-up-central-hub-dns).

## Set up central hub DNS

These steps use the Azure CLI. The portal equivalents live under **Private DNS zones** and **DNS Private Resolver**. The same objects map to your own Terraform or Bicep if the hub is codified. These steps replace Steps 5 and 6 of the manual guide.

Set the hub values:

```bash
export HUB_RG="<hub-resource-group>"           # owns the private DNS zones
export ZONE="databases.neo4j.io"
export AURA_INSTANCE_ID="<aura-instance-id>"   # the label only, for example abcd1234
```

Read the endpoint private IP into `PE_IP`. On the Terraform path, run this from `infra/terraform/azure-private-endpoint` after `terraform apply`:

```bash
export PE_IP="$(terraform output -raw private_endpoint_nic_ip)"
```

On the manual path, use the commands from [Step 4 of the manual guide](../setup-private-link-manual.md#step-4-check-the-connection-status). `RG` and `PE_NAME` are the resource group and name of the endpoint, not the hub:

```bash
NIC_ID="$(az network private-endpoint show --name "$PE_NAME" --resource-group "$RG" \
  --query "networkInterfaces[0].id" -o tsv)"
export PE_IP="$(az network nic show --ids "$NIC_ID" \
  --query "ipConfigurations[0].privateIPAddress" -o tsv)"
echo "$PE_IP"
```

### Step 1: Confirm the hub owns the zone

The platform team normally runs this zone already. Create it only if it does not exist, and only in the hub resource group:

```bash
az network private-dns zone show --resource-group "$HUB_RG" --name "$ZONE" \
  || az network private-dns zone create --resource-group "$HUB_RG" --name "$ZONE"
```

### Step 2: Link the zone to every consuming VNet

Each spoke VNet whose workloads reach Aura must resolve against the hub zone. Add one link per spoke. Auto-registration stays off because you manage the records by hand:

```bash
az network private-dns link vnet create \
  --resource-group "$HUB_RG" \
  --zone-name "$ZONE" \
  --name "link-<spoke-name>" \
  --virtual-network "<spoke-vnet-resource-id>" \
  --registration-enabled false
```

The hub may front DNS with Azure DNS Private Resolver. In that model the spokes usually hold no per-zone links. They point their VNet DNS at the resolver inbound endpoint, and a forwarding ruleset sends `databases.neo4j.io` to the hub. The standing platform pattern already covers this step, so continue to Step 3.

### Step 3: Add the instance A record

Map the Aura instance label to the endpoint private IP in the hub zone. The TTL of 30 seconds matches the script and the Terraform stack:

```bash
az network private-dns record-set a create \
  --resource-group "$HUB_RG" --zone-name "$ZONE" \
  --name "$AURA_INSTANCE_ID" --ttl 30

az network private-dns record-set a add-record \
  --resource-group "$HUB_RG" --zone-name "$ZONE" \
  --record-set-name "$AURA_INSTANCE_ID" \
  --ipv4-address "$PE_IP"
```

### Step 4: Add routing-host records when a client needs them

Aura VDC returns Bolt routing addresses that look like `p-<aura-instance-id>-<suffix>.<orch>.neo4j.io`. The `ncc-notebooks/` set maps those hosts back to the instance host, so it passes without these records. The `pl-notebooks/` set uses plain DNS by default, so it fails until they exist. A client without such a resolver reports `Cannot resolve address p-...neo4j.io:7687`, and the error names the host. Your own apps and jobs, `neo4j-cli`, and Neo4j Desktop are examples of such clients.

Routing hosts sit under `<orch>.neo4j.io`, not under `databases.neo4j.io`, so they need their own zone in the hub. Set the host from the error message, then derive the zone and the record name from it:

```bash
export ROUTING_HOST="p-<aura-instance-id>-<suffix>.<orch>.neo4j.io"
export ORCH_ZONE="${ROUTING_HOST#*.}"       # everything after the first label
export ROUTING_LABEL="${ROUTING_HOST%%.*}"  # the first label
```

Create the zone in the hub and link it to every consuming VNet, as in Step 2:

```bash
az network private-dns zone create --resource-group "$HUB_RG" --name "$ORCH_ZONE"

az network private-dns link vnet create \
  --resource-group "$HUB_RG" \
  --zone-name "$ORCH_ZONE" \
  --name "link-<spoke-name>" \
  --virtual-network "<spoke-vnet-resource-id>" \
  --registration-enabled false
```

If the hub uses Azure DNS Private Resolver, make sure `<orch>.neo4j.io` reaches the hub the same way `databases.neo4j.io` does.

Add one record set and one A record per routing host:

```bash
az network private-dns record-set a create \
  --resource-group "$HUB_RG" --zone-name "$ORCH_ZONE" \
  --name "$ROUTING_LABEL" --ttl 30

az network private-dns record-set a add-record \
  --resource-group "$HUB_RG" --zone-name "$ORCH_ZONE" \
  --record-set-name "$ROUTING_LABEL" \
  --ipv4-address "$PE_IP"
```

Repeat the record commands for every routing host a client reports. A linked zone answers for the whole `<orch>.neo4j.io` domain. Any host in that domain without a record stops resolving from linked VNets.

Do not use a `p-*` record. An Azure private DNS wildcard must be the whole label `*`, so `p-*` matches nothing. A `*` record would point every host under that domain at your endpoint, so add one record per host instead.

### Step 5: Check resolution from a consuming VNet

`private_link.py verify` checks only zones in `$RG` that carry the link names the script uses. It does not cover a hub zone. Check from a VM, cluster, or pod in a linked VNet instead:

```bash
nslookup "${AURA_INSTANCE_ID}.databases.neo4j.io"   # must return the endpoint private IP
nslookup "$ROUTING_HOST"                            # each routing host, same private IP
```

A public address means the VNet is not linked to the hub zone, or the resolver forwarding rule is missing. Revisit Step 2.

Then open a Bolt connection with `neo4j+s://<aura-instance-id>.databases.neo4j.io` from a Neo4j client in the linked VNet. Continue with the rest of Validate connectivity in the [manual guide](../setup-private-link-manual.md#validate-connectivity) or the [Terraform guide](../setup-private-link-terraform.md#validate-connectivity).
