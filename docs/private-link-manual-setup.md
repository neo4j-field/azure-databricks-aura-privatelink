# Manual Private Link setup

This guide creates the Azure side of the private path by hand: a private endpoint in your VNet that connects to the Aura Private Link service, and the private DNS that makes the Aura hostname resolve to it. It produces the same result as the [`azure-private-endpoint` Terraform stack](../infra/terraform/azure-private-endpoint/), without Terraform.

Use this path when the consumer runs in your own VNet. Typical fits are classic Azure Databricks clusters, AKS, Azure Data Factory self-hosted IR, and jump VMs. For Azure Databricks Serverless, use the [manual NCC setup](manual-ncc-setup.md) instead. Serverless compute runs in Databricks-managed subscriptions and cannot use a private endpoint in your VNet.

Each step shows the Azure CLI command first. The portal alternative follows where it helps.

## What Terraform does for you

| Terraform resource | Manual step |
|--------------------|-------------|
| `azurerm_private_endpoint.aura` | [Step 2: Create the private endpoint](#step-2-create-the-private-endpoint) |
| `azurerm_private_dns_zone.neo4j` | [Step 5: Create the private DNS zone and link it](#step-5-create-the-private-dns-zone-and-link-it) |
| `azurerm_private_dns_zone_virtual_network_link.neo4j` | [Step 5: Create the private DNS zone and link it](#step-5-create-the-private-dns-zone-and-link-it) |
| `private_dns_zone_group` on the endpoint | [Step 5: Create the private DNS zone and link it](#step-5-create-the-private-dns-zone-and-link-it) |
| `azurerm_private_dns_a_record.aura` | [Step 6: Add the instance A record](#step-6-add-the-instance-a-record) |

Terraform does not wait for Aura to approve the endpoint. It also does not create records for routing hosts. The steps after Step 6 cover those.

## Scripted alternative

[`scripts/private_link.py`](../scripts/private_link.py) runs the same `az` commands as this guide. It prints each command before it runs, so the output reads as a transcript of the manual steps. It reads the same variables that this guide exports, and it is safe to run again.

```bash
uv run scripts/private_link.py run
```

`run` creates the endpoint and waits for the Aura approval. If the approval has not happened yet, it stops with exit code 2 and tells you what to click. Run it again after you approve, and it builds the DNS. The other commands map to single steps: `create`, `status --wait`, `dns`, `add-routing-host`, `verify`, and `destroy`.

Add `--dry-run` to `create`, `dns`, `add-routing-host`, `run`, or `destroy` to change nothing. The script prints each resource as `OK`, `CREATE`, `UPDATE`, `DELETE`, or `WAIT`, so you can see what exists and what a real run would change.

```bash
uv run scripts/private_link.py run --dry-run
```

`verify` compares every A record to the endpoint IP. Add `--vm <name>` to resolve each host from a VM in the VNet. Add `--bolt` to run `RETURN 1` through [`neo4j-cli`](https://github.com/neo4j/neo4j-cli) against `neo4j+s://<instance-id>.databases.neo4j.io`. Pass `--bolt-credential <name>` or `--bolt-env <file>` for the login. The check reports whether the host resolves to a private or a public address, so a pass from a laptop is not mistaken for a pass on the private path.

The script covers the single-VNet DNS mode only. For a central hub DNS, follow [Private DNS: self-managed vs. central](private-endpoint-stack-setup.md#private-dns-self-managed-vs-central-hub-and-spoke).

To test the script without Aura, [`scripts/private_link_testbed.py`](../scripts/private_link_testbed.py) builds a stand-in Private Link service with manual approval in a throwaway resource group. Its `up --vm` command builds the stand-in and a VM to resolve DNS from. `approve` plays the part of the Aura console. `down` deletes the resource group, and it refuses any group that lacks the testbed tag.

## Before you start

You need the Aura values from [README Steps 1 and 2](../README.md#step-1-provision-neo4j-aura-vdc-on-azure). You also need an existing VNet and a subnet that will host the endpoint NIC. The subnet must allow private endpoints, which is the default for modern subnets.

Your own Azure subscription ID must be in the Aura network access configuration under **Target Azure Subscription IDs**. Without it, the connection request never appears in Aura. Use your own subscription here, not a Databricks-managed one.

Sign in to Azure as a user who can create private endpoints and private DNS zones in the target resource group:

```bash
az login --tenant "<tenant-id>"
az account set --subscription "<subscription-id>"
```

Set the values the commands below reuse:

```bash
export RG="<resource-group>"                     # holds the endpoint and the DNS zone
export VNET="<vnet-name>"
export VNET_RG="<vnet-resource-group>"           # use the same value as RG if they match
export PE_SUBNET="<subnet-name>"
export PE_NAME="pe-<aura-instance-id>-<region>"
export AURA_PLS_ALIAS="pls-<id>.<guid>.<region>.azure.privatelinkservice"
export AURA_INSTANCE_ID="<aura-instance-id>"     # the label only, for example abcd1234
export ZONE="databases.neo4j.io"
```

`AURA_PLS_ALIAS` is the Private Link service name from the Aura console. `AURA_INSTANCE_ID` is the first label of the Aura hostname, so `abcd1234` for `abcd1234.databases.neo4j.io`.

Look up the subnet and VNet resource IDs. This works when the VNet sits in a different resource group from the endpoint:

```bash
export SUBNET_ID="$(az network vnet subnet show --resource-group "$VNET_RG" \
  --vnet-name "$VNET" --name "$PE_SUBNET" --query id -o tsv)"
export VNET_ID="$(az network vnet show --resource-group "$VNET_RG" \
  --name "$VNET" --query id -o tsv)"
echo "$SUBNET_ID"
```

## Step 1: Confirm Aura trusts your subscription

Open the Aura console and check **Project settings → Security & Networking → Private endpoints**. Your subscription ID must be listed under **Target Azure Subscription IDs**, as in [README Step 2](../README.md#step-2-enable-private-link-in-aura-network-access-configuration).

Print the subscription ID you are signed in to, and compare it with the Aura list:

```bash
az account show --query id -o tsv
```

## Step 2: Create the private endpoint

The endpoint must be in the same region as the VNet. The target Private Link service can be in any region.

```bash
az network private-endpoint create \
  --name "$PE_NAME" \
  --resource-group "$RG" \
  --subnet "$SUBNET_ID" \
  --nic-name "${PE_NAME}-nic" \
  --connection-name "${PE_NAME}-conn" \
  --private-connection-resource-id "$AURA_PLS_ALIAS" \
  --manual-request true \
  --request-message "Neo4j Aura Private Link from <team> / <subscription nickname>"
```

Three details matter here:

- `--private-connection-resource-id` takes the Aura PLS alias. Aura is a third-party service, so there is no ARM resource ID on your side.
- `--manual-request true` is required. The consumer side cannot auto-approve a connection to a Private Link service in another subscription.
- Do not set `--group-id`. It is for first-party Azure resources.

The request message appears on the Aura approval screen. Make it identify your team so the Aura admin can match the request.

Portal alternative: **Private endpoints → Create**. On the **Resource** tab, choose **Connect to an Azure resource by resource ID or alias** and paste the alias. Tick **Request message** and fill it in.

## Step 3: Approve the endpoint in Aura

Approve the incoming request in the Aura console, as in [README Step 7](../README.md#step-7-approve-the-private-endpoint-in-the-aura-console). Approve within a day. A connection that stays `Pending`, `Rejected`, or `Disconnected` for 14 days expires.

## Step 4: Check the connection status

Poll the connection until it reads `Approved`:

```bash
az network private-endpoint show --name "$PE_NAME" --resource-group "$RG" \
  --query "manualPrivateLinkServiceConnections[0].privateLinkServiceConnectionState.{status:status, description:description}" \
  -o json
```

`Pending` means Aura has not approved the request yet. `Rejected` or `Disconnected` means you must [recreate the endpoint](#recreate-a-rejected-or-disconnected-endpoint).

Read the private IP that Azure assigned to the endpoint NIC. The DNS records point at this address:

```bash
NIC_ID="$(az network private-endpoint show --name "$PE_NAME" --resource-group "$RG" \
  --query "networkInterfaces[0].id" -o tsv)"
export PE_IP="$(az network nic show --ids "$NIC_ID" \
  --query "ipConfigurations[0].privateIPAddress" -o tsv)"
echo "$PE_IP"
```

The address comes from the subnet range, for example `10.x.x.x`.

## Step 5: Create the private DNS zone and link it

The endpoint gives you a private IP, but nothing yet connects the Aura hostname to it. A private DNS zone named `databases.neo4j.io` overrides the public answer inside your network. It applies only to the VNets you link, and it changes nothing for the rest of the world. See [Why `databases.neo4j.io` lives in your private DNS zone](private-endpoint-stack-setup.md#why-databasesneo4jio-lives-in-your-private-dns-zone) for the reasoning.

Create the zone:

```bash
az network private-dns zone create --resource-group "$RG" --name "$ZONE"
```

Link it to the VNet. Auto-registration stays off because you manage the records by hand:

```bash
az network private-dns link vnet create \
  --resource-group "$RG" \
  --zone-name "$ZONE" \
  --name "${PE_NAME}-vnet-link" \
  --virtual-network "$VNET_ID" \
  --registration-enabled false
```

Attach the zone to the endpoint with a zone group. This matches what the Terraform stack does:

```bash
az network private-endpoint dns-zone-group create \
  --resource-group "$RG" \
  --endpoint-name "$PE_NAME" \
  --name default \
  --zone-name "$ZONE" \
  --private-dns-zone "$ZONE"
```

Each VNet whose workloads reach Aura needs its own link. Repeat the link command with a different `--name` and `--virtual-network` for every extra VNet.

Your organization may run DNS centrally in a hub VNet. In that case do not create the zone here. Follow [Private DNS: self-managed vs. central](private-endpoint-stack-setup.md#private-dns-self-managed-vs-central-hub-and-spoke) and skip to Step 6 with the hub resource group.

## Step 6: Add the instance A record

Map the Aura instance label to the endpoint private IP. Terraform sets a TTL of 30 seconds, so this step does the same:

```bash
az network private-dns record-set a create \
  --resource-group "$RG" --zone-name "$ZONE" \
  --name "$AURA_INSTANCE_ID" --ttl 30

az network private-dns record-set a add-record \
  --resource-group "$RG" --zone-name "$ZONE" \
  --record-set-name "$AURA_INSTANCE_ID" \
  --ipv4-address "$PE_IP"
```

Without this record, the hostname falls back to Aura's public IP.

## Step 7: Verify DNS and connectivity

Run these from a VM or cluster inside a linked VNet:

```bash
nslookup "${AURA_INSTANCE_ID}.databases.neo4j.io"
nc -vz -w 5 "${AURA_INSTANCE_ID}.databases.neo4j.io" 7687
```

The `nslookup` answer must be the endpoint private IP. A public address means the VNet is not linked to the zone, or the A record is missing. Then open a Bolt connection with `neo4j+s://<aura-instance-id>.databases.neo4j.io` to confirm end-to-end traffic.

If the first connection fails with `Cannot resolve address p-...neo4j.io:7687`, continue to the next section.

## Add routing-host records

Aura VDC returns Bolt routing addresses after the first connection. They look like `p-<dbid>-<suffix>.<orch>.neo4j.io`. These hosts sit under `<orch>.neo4j.io`, not under `databases.neo4j.io`. A record in the `databases.neo4j.io` zone never matches them, so each one needs its own record in a separate zone.

Set the routing host from the error message, then derive the zone and the record name from it:

```bash
export ROUTING_HOST="p-<dbid>-<suffix>.<orch>.neo4j.io"
export ORCH_ZONE="${ROUTING_HOST#*.}"       # everything after the first label
export ROUTING_LABEL="${ROUTING_HOST%%.*}"  # the first label
```

Create the zone and link it to the same VNet:

```bash
az network private-dns zone create --resource-group "$RG" --name "$ORCH_ZONE"

az network private-dns link vnet create \
  --resource-group "$RG" \
  --zone-name "$ORCH_ZONE" \
  --name "${PE_NAME}-orch-link" \
  --virtual-network "$VNET_ID" \
  --registration-enabled false
```

Add one A record per routing host. Each record points at the same endpoint private IP:

```bash
az network private-dns record-set a add-record \
  --resource-group "$RG" --zone-name "$ORCH_ZONE" \
  --record-set-name "$ROUTING_LABEL" \
  --ipv4-address "$PE_IP"
```

Repeat the last command for every routing host the driver reports. The zone and link are created once. A linked zone answers for the whole `<orch>.neo4j.io` domain, so any host in that domain without a record stops resolving from linked VNets.

Do not use a `p-*` record. An Azure private DNS wildcard must be the whole label `*`, so `p-*` matches nothing.

## Step 8: Close the public endpoint

Private Link adds a private path. It does not close the public one. Disable public access in the Aura console, as in [README Step 9](../README.md#step-9-disable-public-access-on-aura). Run the outside-in check from that step, then repeat the verification in Step 7.

Disable public access only after validation succeeds. Doing it earlier can lock you out while you debug.

## Recreate a rejected or disconnected endpoint

Delete the endpoint, then repeat [Step 2](#step-2-create-the-private-endpoint) through [Step 4](#step-4-check-the-connection-status):

```bash
az network private-endpoint delete --name "$PE_NAME" --resource-group "$RG"
```

A new endpoint can receive a different private IP. Update every A record you created, including the routing-host records, or the hostnames resolve to an address that no longer answers:

```bash
az network private-dns record-set a remove-record \
  --resource-group "$RG" --zone-name "$ZONE" \
  --record-set-name "$AURA_INSTANCE_ID" --ipv4-address "<old-ip>"

az network private-dns record-set a add-record \
  --resource-group "$RG" --zone-name "$ZONE" \
  --record-set-name "$AURA_INSTANCE_ID" --ipv4-address "$PE_IP"
```

## Teardown

Delete only what you created in this guide. Never delete a hub zone that other teams use.

```bash
az network private-endpoint delete --name "$PE_NAME" --resource-group "$RG"

az network private-dns link vnet delete --resource-group "$RG" \
  --zone-name "$ZONE" --name "${PE_NAME}-vnet-link" --yes
az network private-dns zone delete --resource-group "$RG" --name "$ZONE" --yes
```

Deleting the zone removes the A records inside it. If you created an `<orch>.neo4j.io` zone, delete its link and the zone the same way.

Then open the Aura private endpoints page and remove the orphaned approval, as in [Teardown step 4](teardown.md#step-4-aura-side-cleanup-manual-no-api).

## What this guide does not cover

Developer laptops that need Neo4j Desktop or a browser are covered in [Developer desktop access](developer-desktop-access.md). Workloads in other VNets or subscriptions are covered in [Batch jobs in other VNets](batch-jobs-other-vnets.md).
