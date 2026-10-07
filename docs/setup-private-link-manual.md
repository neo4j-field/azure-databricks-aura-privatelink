# Private Link manual setup

This guide creates the Azure side of the private path with the Azure CLI, by hand or with a script, without Terraform: a private endpoint in your VNet that connects to the Aura Private Link service, and the private DNS that makes the Aura hostname resolve to it. It produces the same result as the [`azure-private-endpoint` Terraform stack](../infra/terraform/azure-private-endpoint/).

Use this path when the consumer runs in your own VNet. Typical fits are classic Azure Databricks clusters, AKS, Azure Data Factory self-hosted IR, and jump VMs. For Azure Databricks Serverless, use the [NCC manual setup](setup-ncc-manual.md) instead. Serverless compute runs in Databricks-managed subscriptions and cannot use a private endpoint in your VNet.

## Choose how to run the setup

You can run the Azure CLI commands yourself, or let a script run them for you. Both options build the same resources with the same names.

| | Option A: Script | Option B: Manual commands |
|---|---|---|
| How it works | `uv run scripts/private_link.py run` runs the `az` commands for you. | You copy each `az` command from this guide and run it. |
| Best for | A repeatable setup, a dry-run preview, and reruns after a pause. | Seeing each resource as you create it, using the portal, or using central hub DNS. |
| Azure steps covered | Preflight checks, the private endpoint, the approval wait, and the private DNS for a single VNet. | The same steps, plus portal alternatives and the hub DNS path. |
| DNS modes | Single VNet only. | Single VNet or central hub. |
| Aura console steps | You do them. | You do them. |

Neither option touches the Aura console. In both options you do two things there: allow-list your subscription before you create the endpoint, and accept the connection request after you create it.

Both options start with [Before you start](#before-you-start) and finish with [After setup: what comes next for both options](#after-setup-what-comes-next-for-both-options).

## Contents

- [Before you start](#before-you-start)
- [Option A: Run the script](#option-a-run-the-script)
- [Option B: Run the commands yourself](#option-b-run-the-commands-yourself)
- [Recreate a rejected or disconnected endpoint](#recreate-a-rejected-or-disconnected-endpoint)
- [After setup: what comes next for both options](#after-setup-what-comes-next-for-both-options)
  - [Validate connectivity](#validate-connectivity)
  - [Add routing-host records](#add-routing-host-records)
  - [Close the public endpoint](#close-the-public-endpoint)
  - [Teardown](#teardown)
  - [What's next](#whats-next)

## Before you start

You need the Aura values from [Aura console Steps 1 and 2](shared/aura-console-steps.md#step-1-provision-neo4j-aura-vdc-on-azure). You also need an existing VNet and a subnet that will host the endpoint NIC. The subnet must allow private endpoints, which is the default for modern subnets.

Your own Azure subscription ID must be in the Aura network access configuration under **Target Azure Subscription IDs**. Without it, the connection request never appears in Aura. Use your own subscription here, not a Databricks-managed one.

Sign in to Azure as a user who can create private endpoints and private DNS zones in the target resource group:

```bash
az login --tenant "<tenant-id>"
az account set --subscription "<subscription-id>"
```

Set the values both options reuse. The script reads these same variables:

```bash
export RG="<resource-group>"                     # holds the endpoint and the DNS zone
export VNET="<vnet-name>"
export VNET_RG="<vnet-resource-group>"           # use the same value as RG if they match
export PE_SUBNET="<subnet-name>"
export PE_NAME="pe-<aura-instance-id>-<region>"
export AURA_PLS_ALIAS="production-orch-<id>-service.<guid>.<region>.azure.privatelinkservice"
export AURA_INSTANCE_ID="<aura-instance-id>"     # the label only, for example abcd1234
export REQUEST_MESSAGE="Neo4j Aura Private Link from <team> / <subscription nickname>"  # read by the script
export ZONE="databases.neo4j.io"
```

`AURA_PLS_ALIAS` is the Private Link service name from the Aura console. `AURA_INSTANCE_ID` is the first label of the Aura hostname, so `abcd1234` for `abcd1234.databases.neo4j.io`. `REQUEST_MESSAGE` appears on the Aura approval screen. Make it identify your team so the Aura admin can match the request. The script falls back to a generic message when you leave it unset.

**Where to find the network values.** These four describe your existing Azure network, so look them up rather than invent them:

| Variable | What it is | Where to find it |
|----------|------------|------------------|
| `VNET` | Name of the VNet that holds the consumers. For classic Databricks, the workspace VNet. | Azure portal, Virtual networks. For a VNet-injected Databricks workspace, open the workspace Overview and read **Virtual network**. |
| `VNET_RG` | Resource group that contains `VNET` | Azure portal, the VNet Overview page, **Resource group** |
| `RG` | Resource group that will hold the private endpoint and the private DNS zone | Your choice. Use `VNET_RG` unless your team keeps networking resources elsewhere. |
| `PE_SUBNET` | Name of a subnet in `VNET` for the private endpoint | Azure portal, the VNet, **Subnets**. Pick a subnet that is not delegated, or create a new one. |

To list them from the CLI:

```bash
az network vnet list --query "[].{vnet:name, resourceGroup:resourceGroup}" -o table
az network vnet subnet list --resource-group "$VNET_RG" --vnet-name "$VNET" \
  --query "[].{subnet:name, delegation:delegations[0].serviceName}" -o table
```

Run the second command after you set `VNET` and `VNET_RG`. A subnet with a value in the `delegation` column cannot hold a private endpoint.

### Classic Databricks workspaces

A classic Databricks consumer adds a few requirements:

- **Workspace type:** The workspace must be VNet-injected. You choose VNet injection when you create the workspace. A workspace in a Databricks-managed VNet cannot use this path, so use the [NCC manual setup](setup-ncc-manual.md) instead.
- **Endpoint subnet:** `PE_SUBNET` must be a separate subnet. The Databricks host and container subnets are delegated to `Microsoft.Databricks/workspaces`, and they cannot hold a private endpoint.
- **VNet:** Set `VNET` to the workspace VNet. If the endpoint lives in another VNet, link the zone to the workspace VNet and peer the two VNets, as in [Batch jobs in other VNets](operations/batch-jobs-other-vnets.md).
- **Custom DNS:** If the workspace VNet uses custom DNS servers, forward `databases.neo4j.io` and any `<orch>.neo4j.io` zones to `168.63.129.16`. Only that Azure resolver answers from your private DNS zones.
- **Outbound rules:** If you tightened the outbound NSG rules on the Databricks subnets, allow TCP 7687 to the endpoint subnet.

## Option A: Run the script

[`scripts/private_link.py`](../scripts/private_link.py) runs the same `az` commands as Option B. It prints each command before it runs, so the output reads as a transcript of the manual steps. Every command inspects what exists and changes only the difference, so it is safe to run again.

### What the script does and what you do

`run` does the Azure work in three stages:

1. It checks your Azure login, the resource group, the subnet, and the format of the PLS alias. Then it creates the private endpoint.
2. It waits for the Aura approval.
3. It creates the private DNS zone, the VNet link, the zone group, and the A record for the instance.

You do the rest:

- **Before `run`:** Allow-list your subscription in the Aura console. The script prints your subscription ID but cannot read the Aura allow list, so it does not confirm that Aura trusts you. See [Step 1 of Option B](#step-1-confirm-aura-trusts-your-subscription).
- **During `run`:** Accept the connection request in the Aura console.
- **After `run`:** Add records for routing hosts, validate, and close public access. The script has commands for the first two. See [After setup](#after-setup-what-comes-next-for-both-options).

### Run it

Preview first. A dry run changes nothing:

```bash
uv run scripts/private_link.py run --dry-run
```

It prints one row per resource, so you can see what exists and what a real run would change:

| State | Meaning |
|-------|---------|
| `OK` | Exists and is correct. |
| `CREATE` | Would be created. |
| `UPDATE` | Exists but differs. |
| `DELETE` | Would be deleted. |
| `WAIT` | Waits on a step in the Aura console. |
| `CHECK` | Cannot be judged until the endpoint exists. |
| `KEEP` | The zone stays because another link still uses it. |
| `ERROR` | Blocks the setup. |

A dry run exits with `1` when any row is `ERROR`.

Then run it:

```bash
uv run scripts/private_link.py run
```

`run` creates the endpoint, then polls for the Aura approval every 15 seconds for up to 10 minutes. Change these with `--interval` and `--timeout`, both in seconds. If the approval has not happened by then, the script stops with exit code 2 and prints what to click. Accept the request in the Aura console and run the same command again. This time it skips the endpoint, sees the approval, and builds the DNS.

Exit codes: `0` means done, `1` means an error or a failed check, and `2` means the script paused for a step in the Aura console.

### Other commands

Each command maps to one part of the setup:

| Command | What it does |
|---------|--------------|
| `create` | Creates the private endpoint. |
| `status --wait` | Waits for the Aura approval and prints the endpoint IP. |
| `dns` | Creates the zone, VNet link, zone group, and instance A record. |
| `add-routing-host <host>` | Adds the zone, link, and A record for one routing host. |
| `verify` | Compares every A record to the endpoint IP. It checks only the zones the script links, so it covers the single-VNet mode. |
| `destroy` | Deletes the endpoint and the DNS objects the script created. |

Add `--dry-run` to `create`, `dns`, `add-routing-host`, `run`, or `destroy` to preview the change. The other commands do not accept it.

### Limits

The script covers the single-VNet DNS mode only. For a central hub DNS, use Option B and follow [Set up central hub DNS](shared/private-dns-central.md#set-up-central-hub-dns).

To test the script without Aura, [`scripts/private_link_testbed.py`](../scripts/private_link_testbed.py) builds a stand-in Private Link service with manual approval in a throwaway resource group. Its `up --vm` command builds the stand-in and a VM to resolve DNS from. `approve` plays the part of the Aura console. `down` deletes the resource group, and it refuses any group that lacks the testbed tag.

When `run` finishes, continue at [After setup: what comes next for both options](#after-setup-what-comes-next-for-both-options).

## Option B: Run the commands yourself

Each step shows the Azure CLI command first. The portal alternative follows where it helps. Finish Steps 1 to 6, then continue at [After setup: what comes next for both options](#after-setup-what-comes-next-for-both-options).

Look up the subnet and VNet resource IDs. This works when the VNet sits in a different resource group from the endpoint:

```bash
export SUBNET_ID="$(az network vnet subnet show --resource-group "$VNET_RG" \
  --vnet-name "$VNET" --name "$PE_SUBNET" --query id -o tsv)"
export VNET_ID="$(az network vnet show --resource-group "$VNET_RG" \
  --name "$VNET" --query id -o tsv)"
echo "$SUBNET_ID"
```

### Step 1: Confirm Aura trusts your subscription

Open the Aura console and check **Project settings → Security & Networking → Private endpoints**. Your subscription ID must be listed under **Target Azure Subscription IDs**, as in [Aura console Step 3](shared/aura-console-steps.md#step-3-allow-list-the-consumer-subscription).

Print the subscription ID you are signed in to, and compare it with the Aura list:

```bash
az account show --query id -o tsv
```

### Step 2: Create the private endpoint

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
  --request-message "$REQUEST_MESSAGE"
```

Three details matter here:

- `--private-connection-resource-id` takes the Aura PLS alias. Aura is a third-party service, so there is no ARM resource ID on your side.
- `--manual-request true` is required. The consumer side cannot auto-approve a connection to a Private Link service in another subscription.
- Do not set `--group-id`. It is for first-party Azure resources.

The request message appears on the Aura approval screen.

Portal alternative: **Private endpoints → Create**. On the **Resource** tab, choose **Connect to an Azure resource by resource ID or alias** and paste the alias. Tick **Request message** and fill it in.

### Step 3: Approve the endpoint in Aura

Approve the incoming request in the Aura console, as in [Aura console Step 4](shared/aura-console-steps.md#step-4-approve-the-private-endpoint-in-the-aura-console). The endpoint carries no traffic until Aura approves it.

### Step 4: Check the connection status

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

### Step 5: Create the private DNS zone and link it

The endpoint gives you a private IP, but nothing yet connects the Aura hostname to it. A private DNS zone named `databases.neo4j.io` overrides the public answer inside your network. It applies only to the VNets you link, and it changes nothing for the rest of the world. See [Why databases.neo4j.io lives in your private DNS zone](shared/private-dns-central.md#why-databasesneo4jio-lives-in-your-private-dns-zone) for the reasoning.

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

Your organization may run DNS centrally in a hub VNet. In that case do not create the zone here. Follow [Set up central hub DNS](shared/private-dns-central.md#set-up-central-hub-dns) instead. It replaces Steps 5 and 6, including the instance A record.

### Step 6: Add the instance A record

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

Without this record, lookups from the linked VNet return NXDOMAIN and the connection fails. The Azure setup is now complete. Continue at [After setup: what comes next for both options](#after-setup-what-comes-next-for-both-options).

## Recreate a rejected or disconnected endpoint

This applies to both options. With the script, run `destroy`, then run `run` again. By hand, delete the endpoint, then repeat [Step 2](#step-2-create-the-private-endpoint) through [Step 4](#step-4-check-the-connection-status):

```bash
az network private-endpoint delete --name "$PE_NAME" --resource-group "$RG"
```

A new endpoint can receive a different private IP. Update every A record you created, including the routing-host records, or the hostnames resolve to an address that no longer answers. The script does this for you when you run `dns` or `add-routing-host` again.

By hand, read the new `PE_IP` as in Step 4, then swap the address in each record set. Remove the old address first. `--keep-empty-record-set` keeps the record set and its 30-second TTL while it is empty:

```bash
az network private-dns record-set a remove-record \
  --resource-group "$RG" --zone-name "$ZONE" \
  --record-set-name "$AURA_INSTANCE_ID" --ipv4-address "<old-ip>" \
  --keep-empty-record-set

az network private-dns record-set a add-record \
  --resource-group "$RG" --zone-name "$ZONE" \
  --record-set-name "$AURA_INSTANCE_ID" --ipv4-address "$PE_IP"
```

Repeat both commands for each routing-host record. Use `--zone-name "$ORCH_ZONE"` and the routing label as the record set name.

## After setup: what comes next for both options

Both options leave you with a private endpoint, an approved connection, and an instance A record. Work through these in order:

1. [Validate connectivity](#validate-connectivity). Confirm the hostname resolves to the endpoint IP and a Bolt query succeeds.
2. [Add routing-host records](#add-routing-host-records). Do this when a client reports `Cannot resolve address p-...neo4j.io:7687`. The notebooks pass without these records.
3. [Close the public endpoint](#close-the-public-endpoint). Do this only after validation succeeds.
4. [Teardown](#teardown) when you no longer need the setup.

### Validate connectivity

DNS on this path comes from the private DNS zone you linked to the VNet, not from Databricks. Run every check from a VM, cluster, or pod inside a linked VNet. A machine outside the linked VNets resolves the public address, so a pass there says nothing about the private path.

#### Check DNS and the Bolt port

```bash
nslookup "${AURA_INSTANCE_ID}.databases.neo4j.io"
nc -vz -w 5 "${AURA_INSTANCE_ID}.databases.neo4j.io" 7687
```

The `nslookup` answer must be the endpoint private IP. A public address means the VNet is not linked to the zone, or the A record is missing.

#### Run a Bolt query

**Script.** [`scripts/private_link.py`](../scripts/private_link.py) runs the checks for you. It reads `RG`, `PE_NAME`, and `AURA_INSTANCE_ID` from the variables in [Before you start](#before-you-start):

```bash
uv run scripts/private_link.py verify --bolt
```

`verify` compares every A record to the endpoint IP. It checks only the zones in `$RG` that carry a link named `<PE_NAME>-vnet-link` or `<PE_NAME>-orch-link`, so it covers the single-VNet mode. With central hub DNS, check with `nslookup` from a linked VNet and a Bolt client instead, as in [Check resolution from a consuming VNet](shared/private-dns-central.md#step-5-check-resolution-from-a-consuming-vnet).

Add `--vm <name>` to resolve each host from a VM in the VNet. `--bolt` runs `RETURN 1` through [`neo4j-cli`](https://github.com/neo4j/neo4j-cli) against `neo4j+s://<aura-instance-id>.databases.neo4j.io`. Pass `--bolt-credential <name>` or `--bolt-env <file>` for the login. The check reports whether the host resolves to a private or a public address. A pass from a laptop therefore cannot be mistaken for a pass on the private path.

`neo4j-cli` has no routing-host resolver. A missing routing-host record makes `--bolt` fail with `Cannot resolve address p-...neo4j.io:7687`, and the error names the host. Add a record for it as in [Add routing-host records](#add-routing-host-records).

**Manual.** Open a Bolt connection with `neo4j+s://<aura-instance-id>.databases.neo4j.io` from any Neo4j client in the linked VNet. A client without a routing-host resolver can report `Cannot resolve address p-...neo4j.io:7687`. The error names the host. Add a record for it as in [Add routing-host records](#add-routing-host-records).

#### Classic Databricks clusters

The notebooks also run on classic clusters in a VNet-injected workspace, as long as the workspace VNet is linked to the private DNS zone.

**Create the secret scope.** The notebooks read Neo4j credentials from a secret scope named `neo4j` in the workspace. Copy the sample file and fill it in. The URI host is your Aura Private URI host:

```bash
cp env.sample .env
```

`.env` is gitignored. It holds `WORKSPACE_PROFILE` and the `NEO4J_*` values. Then run the script from the repository root. It creates the scope and stores the `uri`, `username`, `password`, and `database` keys. The `database` key is optional, and the notebooks fall back to `neo4j` without it:

```bash
./scripts/create-secret-scope.sh
```

**Upload the notebooks.** Run this from the repository root. It copies the notebooks into a `neo4j-privatelink` folder in your user area of the workspace:

```bash
: "${WORKSPACE_PROFILE:?WORKSPACE_PROFILE is not set}"
export NOTEBOOK_DIR="/Users/$(databricks --profile "$WORKSPACE_PROFILE" current-user me -o json | jq -r .userName)/neo4j-privatelink"
databricks --profile "$WORKSPACE_PROFILE" workspace mkdirs "$NOTEBOOK_DIR"
for f in notebooks/0*.py; do
  databricks --profile "$WORKSPACE_PROFILE" workspace import "$NOTEBOOK_DIR/$(basename "$f" .py)" \
    --file "$f" --format SOURCE --language PYTHON --overwrite
done
```

Open the `neo4j-privatelink` folder in the workspace and attach each notebook to a classic cluster instead of serverless compute.

**Restart running clusters after DNS changes.** A cluster that resolved the host before the record existed can keep the public answer cached. Restart it after you create or change any A record or zone link.

Run [notebooks/01_validate_connectivity.py](../notebooks/01_validate_connectivity.py). It asserts that the Aura host resolves to a private address. On this path a public answer points to the zone link or the A record, not to an NCC rule. The notebooks map routing hosts back to the instance host, so they pass without routing-host records.

### Add routing-host records

Aura VDC returns Bolt routing addresses after the first connection. They look like `p-<aura-instance-id>-<suffix>.<orch>.neo4j.io`. These hosts sit under `<orch>.neo4j.io`, not under `databases.neo4j.io`. A record in the `databases.neo4j.io` zone never matches them, so each one needs its own record in a separate zone.

The notebooks in this repository map routing hosts back to the instance host, so they never need these records. Clients without such a resolver do need them. Examples are your own apps and jobs, `neo4j-cli`, Neo4j Desktop, and drivers elsewhere. Such a client reports `Cannot resolve address p-...neo4j.io:7687`, and the error names the host. `private_link.py verify --bolt` runs `neo4j-cli`, so it surfaces missing hosts too. You learn the host names from that error, so neither option can create these records during setup.

With central hub DNS, add these records in the hub as in [Add routing-host records when a client needs them](shared/private-dns-central.md#step-4-add-routing-host-records-when-a-client-needs-them).

**Script.** Run this once for each host a client reports. It creates the zone and the link the first time and adds one A record each time:

```bash
uv run scripts/private_link.py add-routing-host "p-<aura-instance-id>-<suffix>.<orch>.neo4j.io"
```

**Manual.** Set the routing host from the error message, then derive the zone and the record name from it:

```bash
export ROUTING_HOST="p-<aura-instance-id>-<suffix>.<orch>.neo4j.io"
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

Add one record set and one A record per routing host. The record set gets the same 30-second TTL as the instance record. Each record points at the same endpoint private IP:

```bash
az network private-dns record-set a create \
  --resource-group "$RG" --zone-name "$ORCH_ZONE" \
  --name "$ROUTING_LABEL" --ttl 30

az network private-dns record-set a add-record \
  --resource-group "$RG" --zone-name "$ORCH_ZONE" \
  --record-set-name "$ROUTING_LABEL" \
  --ipv4-address "$PE_IP"
```

Repeat the record commands for every routing host a client reports. Set `ROUTING_HOST` and `ROUTING_LABEL` again for each host. The zone and link are created once. A linked zone answers for the whole `<orch>.neo4j.io` domain, so any host in that domain without a record stops resolving from linked VNets.

Do not use a `p-*` record. An Azure private DNS wildcard must be the whole label `*`, so `p-*` matches nothing. A `*` record would point every host under that domain at your endpoint, so add one record per host instead.

After you add the records, run the validation again.

### Close the public endpoint

Private Link adds a private path. It does not close the public one. After validation succeeds, disable public access in the Aura console, as in [Aura console Step 5](shared/aura-console-steps.md#step-5-disable-public-access-on-aura). Run the outside-in check from that step, then run the validation again.

Disable public access only after validation succeeds. Doing it earlier can lock you out while you debug.

### Teardown

Follow [Teardown: Private Link, manual](operations/teardown.md#private-link-manual), then the [Aura-side cleanup](operations/teardown.md#aura-side-cleanup-manual-no-api). Teardown removes the private endpoint and the private DNS zone. If you used the script, `uv run scripts/private_link.py destroy` removes the same Azure resources. You still remove the orphaned approval in the Aura console.

### What's next

| Topic | Guide |
|-------|-------|
| Developer laptop needs Neo4j Desktop or a browser after public access is off | [Developer desktop access](operations/developer-desktop-access.md) |
| Batch jobs, pipelines, or services in other VNets or subscriptions | [Batch jobs in other VNets](operations/batch-jobs-other-vnets.md) |
| Something does not resolve or connect | [Troubleshooting](operations/troubleshooting.md) |
| Run it in production | [Production notes](operations/production-notes.md) |
