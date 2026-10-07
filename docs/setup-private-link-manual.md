# Private Link manual setup

This guide creates the Azure side of the private path with the Azure CLI, by hand or with a script: a private endpoint in your VNet that connects to the Aura Private Link service, and the private DNS that makes the Aura hostname resolve to it. It then sets up Databricks and validates the path from a classic cluster.

Use this path when the consumer runs in your own VNet. Typical fits are classic Azure Databricks clusters, AKS, Azure Data Factory self-hosted IR, and jump VMs. For Azure Databricks Serverless, use the [NCC manual setup](setup-ncc-manual.md) instead. Serverless compute runs in Databricks-managed subscriptions and cannot use a private endpoint in your VNet.

> **Your Databricks workspace must be VNet-injected, or this path cannot work.** A private endpoint lives in a VNet you own, and only compute in that VNet can use it. Create the workspace with your own VNet, in two delegated subnets, so classic clusters run in it. A workspace in a Databricks-managed VNet cannot hold a private endpoint, and serverless compute never runs in your VNet. For either, use the [NCC manual setup](setup-ncc-manual.md).
>
> Check a workspace before you start. This prints the workspace VNet ID, and it prints nothing when the workspace is not VNet-injected:
>
> ```bash
> az databricks workspace show --resource-group "<workspace-rg>" --name "<workspace-name>" --query "parameters.customVirtualNetworkId.value" -o tsv
> ```
>
> No VNet-injected workspace yet? [`scripts/databricks_vnet_workspace.py`](../scripts/databricks_vnet_workspace.py) adds the two delegated subnets and creates one in a VNet you already have. See [Classic Databricks workspaces](#classic-databricks-workspaces).

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

Both options start with [Before you start](#before-you-start) and finish with [Databricks setup and validation](#databricks-setup-and-validation).

## Contents

- [Before you start](#before-you-start)
- [Option A: Run the script](#option-a-run-the-script)
- [Option B: Run the commands yourself](#option-b-run-the-commands-yourself)
- [Recreate a rejected or disconnected endpoint](#recreate-a-rejected-or-disconnected-endpoint)
- [Databricks setup and validation](#databricks-setup-and-validation)
  - [Check the Azure side](#check-the-azure-side)
  - [Collect your values](#collect-your-values)
  - [Create the Neo4j secret scope](#create-the-neo4j-secret-scope)
  - [Upload the notebooks](#upload-the-notebooks)
  - [Create a classic cluster](#create-a-classic-cluster)
  - [Run the validation notebook](#run-the-validation-notebook)
  - [Add routing-host records](#add-routing-host-records)
  - [Run the other notebooks](#run-the-other-notebooks)
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

Both options use the same values. The script reads them only from the repo-root `.env` file. It has no flags for them and ignores exported shell variables. Copy `env.sample` to `.env` if you have not yet. [Environment setup](env-setup.md) says where to find each value. Add:

```bash
RG="<resource-group>"                     # holds the endpoint and the DNS zone
VNET="<vnet-name>"
VNET_RG="<vnet-resource-group>"           # use the same value as RG if they match
PE_SUBNET="<subnet-name>"
PE_NAME="pe-<aura-instance-id>-<region>"
AURA_PLS_ALIAS="production-orch-<id>-service.<guid>.<region>.azure.privatelinkservice"
AURA_INSTANCE_ID="<aura-instance-id>"     # the label only, for example abcd1234
REQUEST_MESSAGE="Neo4j Aura Private Link from <team> / <subscription nickname>"
```

For Option B, load the same file into your shell. The loader also sets `ZONE` to `databases.neo4j.io`, which the script keeps as a constant. Run it again after you edit `.env`:

```bash
source scripts/load-env.sh
```

`AURA_PLS_ALIAS` is the Private Link service name from the Aura console. `AURA_INSTANCE_ID` is the first label of the Aura hostname, so `abcd1234` for `abcd1234.databases.neo4j.io`. `REQUEST_MESSAGE` appears on the Aura approval screen. Make it identify your team so the Aura admin can match the request. The script falls back to a generic message when you leave it unset, and Option B reads it from the `.env` you loaded.

**Where to find the network values.** These four describe your existing Azure network, so look them up rather than invent them:

| Variable | What it is | Where to find it |
|----------|------------|------------------|
| `VNET` | Name of the VNet that holds the consumers. For classic Databricks, the workspace VNet. | Azure portal, Virtual networks. For a VNet-injected Databricks workspace, open the workspace Overview and read **Virtual network**. |
| `VNET_RG` | Resource group that contains `VNET` | Azure portal, the VNet Overview page, **Resource group** |
| `RG` | Resource group that will hold the private endpoint and the private DNS zone | Your choice. Use `VNET_RG` unless your team keeps networking resources elsewhere. |
| `PE_SUBNET` | Name of a subnet in `VNET` for the private endpoint | Azure portal, the VNet, **Subnets**. Pick a subnet that is not delegated, or create a new one. |

**What the script fills in for you.** With Option A you only have to set `AURA_PLS_ALIAS` in `.env`. The script discovers the rest when a value is missing, and prints what it found so you can copy it into `.env`. Option B has no discovery. The loader fills in only `AURA_INSTANCE_ID` from `NEO4J_URI`, `VNET_RG` from `RG`, and `ZONE`, so set every other value yourself. This is how the script finds each one:

| Variable | How the script finds it |
|----------|-------------------------|
| `AURA_INSTANCE_ID` | The first label of `NEO4J_URI` |
| `VNET`, `VNET_RG` | The custom VNet of a VNet-injected Databricks workspace. Set `WORKSPACE_NAME` in `.env` to pick the workspace. If you leave it unset, the script lists the VNet-injected workspaces in the subscription and asks which one. |
| `RG` | The same value as `VNET_RG` |
| `PE_SUBNET` | The subnets in `VNET` that are not delegated. It asks when more than one qualifies. |
| `PE_NAME` | An existing `pe-<aura-instance-id>-*` endpoint, otherwise `pe-<aura-instance-id>-<VNet region>` |

A workspace is VNet-injected only if you chose your own VNet when you created it. Otherwise Databricks creates a locked VNet in a managed resource group, which cannot hold a private endpoint, and the script skips that workspace. Use the [NCC manual setup](setup-ncc-manual.md) for it. If a VNet is not a workspace VNet, set `VNET` and `VNET_RG` yourself. Without a terminal to ask on, the script stops and lists the options instead.

To list the same values from the CLI:

```bash
az network vnet list --query "[].{vnet:name, resourceGroup:resourceGroup}" -o table
az network vnet subnet list --resource-group "$VNET_RG" --vnet-name "$VNET" \
  --query "[].{subnet:name, delegation:delegations[0].serviceName}" -o table
```

Run the second command after you set `VNET` and `VNET_RG`. A subnet with a value in the `delegation` column cannot hold a private endpoint.

### Classic Databricks workspaces

A classic Databricks consumer adds a few requirements:

- **Workspace type:** The workspace must be VNet-injected. You choose VNet injection when you create the workspace. A workspace in a Databricks-managed VNet cannot use this path, so use the [NCC manual setup](setup-ncc-manual.md) instead. To create a VNet-injected workspace in an existing VNet, run `uv run scripts/databricks_vnet_workspace.py up --dry-run`, then `up`. It creates `nsg-dbx`, the two delegated subnets `snet-dbx-host` and `snet-dbx-container`, and a Premium workspace named by `WORKSPACE_NAME` in `.env`. The subnets use `10.10.10.0/24` and `10.10.11.0/24` unless you pass `--host-cidr` and `--container-cidr`, so pick ranges inside your VNet that nothing else uses. The VNet needs a third subnet for the private endpoint.
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
- **After `run`:** Set up Databricks, validate, add records for routing hosts, and close public access. The script has a command for the routing-host records. See [Databricks setup and validation](#databricks-setup-and-validation).

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

When `run` finishes, continue at [Databricks setup and validation](#databricks-setup-and-validation).

## Option B: Run the commands yourself

Each step shows the Azure CLI command first. The portal alternative follows where it helps. Finish Steps 1 to 6, then continue at [Databricks setup and validation](#databricks-setup-and-validation).

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

Attach the zone to the endpoint with a zone group. This ties the zone to the endpoint, so the endpoint's DNS configuration shows which zone serves it. The zone group does not create the Aura instance record. Step 6 adds that by hand:

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

Map the Aura instance label to the endpoint private IP. The record set uses a TTL of 30 seconds. A short TTL limits how long clients keep the old address if you [recreate the endpoint](#recreate-a-rejected-or-disconnected-endpoint) and it gets a different IP:

```bash
az network private-dns record-set a create \
  --resource-group "$RG" --zone-name "$ZONE" \
  --name "$AURA_INSTANCE_ID" --ttl 30

az network private-dns record-set a add-record \
  --resource-group "$RG" --zone-name "$ZONE" \
  --record-set-name "$AURA_INSTANCE_ID" \
  --ipv4-address "$PE_IP"
```

Without this record, lookups from the linked VNet return NXDOMAIN and the connection fails. The Azure setup is now complete. Continue at [Databricks setup and validation](#databricks-setup-and-validation).

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

## Databricks setup and validation

The Azure side is done. Both options leave you with a private endpoint, an approved connection, and an instance A record. The rest of this guide sets up Databricks and proves the path works. Work through these in order:

1. [Check the Azure side](#check-the-azure-side). Confirm the hostname resolves to the endpoint IP.
2. [Collect your values](#collect-your-values). Gather the workspace URL, the CLI profile, and the endpoint IP.
3. [Create the Neo4j secret scope](#create-the-neo4j-secret-scope) in the workspace.
4. [Upload the notebooks](#upload-the-notebooks).
5. [Create a classic cluster](#create-a-classic-cluster).
6. [Run the validation notebook](#run-the-validation-notebook). The first run is expected to fail on routing hosts.
7. [Add routing-host records](#add-routing-host-records) for the hosts the notebook names, then restart the cluster and run the notebook again.
8. [Run the other notebooks](#run-the-other-notebooks).
9. [Close the public endpoint](#close-the-public-endpoint). Do this only after validation succeeds.
10. [Teardown](#teardown) when you no longer need the setup.

> **The workspace must be VNet-injected.** Every Databricks step below runs on a classic cluster in your VNet. A workspace in a Databricks-managed VNet cannot use this path, and neither can serverless compute. Use the [NCC manual setup](setup-ncc-manual.md) for those.

### Check the Azure side

DNS on this path comes from the private DNS zone you linked to the VNet, not from Databricks. The `nslookup` and `nc` checks need a VM or pod inside a linked VNet. A machine outside the linked VNets resolves the public address, so a pass there says nothing about the private path. If you have no such machine yet, run `verify` below and go on to [Collect your values](#collect-your-values). Notebook `01` repeats the DNS and Bolt checks from the cluster.

#### Check DNS and the Bolt port

```bash
nslookup "${AURA_INSTANCE_ID}.databases.neo4j.io"
nc -vz -w 5 "${AURA_INSTANCE_ID}.databases.neo4j.io" 7687
```

The `nslookup` answer must be the endpoint private IP. A public address means the VNet is not linked to the zone, or the A record is missing.

#### Run a Bolt query

**Script.** [`scripts/private_link.py`](../scripts/private_link.py) runs the checks for you. It reads `RG`, `PE_NAME`, and `AURA_INSTANCE_ID` from `.env`, as set in [Before you start](#before-you-start):

```bash
uv run scripts/private_link.py verify --bolt
```

`verify` compares every A record to the endpoint IP. It checks only the zones in `$RG` that carry a link named `<PE_NAME>-vnet-link` or `<PE_NAME>-orch-link`, so it covers the single-VNet mode. With central hub DNS, check with `nslookup` from a linked VNet and a Bolt client instead, as in [Check resolution from a consuming VNet](shared/private-dns-central.md#step-5-check-resolution-from-a-consuming-vnet).

Add `--vm <name>` to resolve each host from a VM in the VNet. `--bolt` runs `RETURN 1` through [`neo4j-cli`](https://github.com/neo4j/neo4j-cli) against `neo4j+s://<aura-instance-id>.databases.neo4j.io`. Pass `--bolt-credential <name>` or `--bolt-env <file>` for the login. The check reports whether the host resolves to a private or a public address. A pass from a laptop therefore cannot be mistaken for a pass on the private path.

`neo4j-cli` has no routing-host resolver. A missing routing-host record makes `--bolt` fail with `Cannot resolve address p-...neo4j.io:7687`, and the error names the host. Add a record for it as in [Add routing-host records](#add-routing-host-records).

**Manual.** Open a Bolt connection with `neo4j+s://<aura-instance-id>.databases.neo4j.io` from any Neo4j client in the linked VNet. A client without a routing-host resolver can report `Cannot resolve address p-...neo4j.io:7687`. The error names the host. Add a record for it as in [Add routing-host records](#add-routing-host-records).

### Collect your values

The next steps need four values. Gather them now and keep them in `.env` or in your shell. The commands below use the shell variable names.

| Value | What it is | How to find it |
|-------|------------|----------------|
| `WS_URL` | The workspace URL | `az databricks workspace show --resource-group "$WS_RG" --name "$WORKSPACE_NAME" --query workspaceUrl -o tsv`, with `https://` in front |
| `WORKSPACE_PROFILE` | A Databricks CLI profile for that workspace | Create it with `databricks auth login`, as below |
| `PE_IP` | The private endpoint IP. The notebooks take it as the `expected_pe_ip` widget. | The `az network nic show` command below. `private_link.py verify` prints it as `endpoint IP`. |
| Neo4j credentials | `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` | The Aura console. You set them in `.env` in [Create the Neo4j secret scope](#create-the-neo4j-secret-scope). |

`az databricks` is an Azure CLI extension. Install it once with `az extension add --name databricks`.

Set `WORKSPACE_NAME` and `WS_RG` in `.env`, load it, then read the URL. This also confirms the workspace is VNet-injected. The last command prints the workspace VNet ID, and it prints nothing for a workspace in a Databricks-managed VNet:

```bash
source scripts/load-env.sh
export WS_URL="https://$(az databricks workspace show --resource-group "$WS_RG" --name "$WORKSPACE_NAME" --query workspaceUrl -o tsv)"
echo "$WS_URL"
az databricks workspace show --resource-group "$WS_RG" --name "$WORKSPACE_NAME" --query "parameters.customVirtualNetworkId.value" -o tsv
```

Create a CLI profile for the workspace. A browser window opens for the login. Pick a profile name, then check it:

```bash
databricks auth login --host "$WS_URL" --profile "<profile-name>"
databricks current-user me --profile "<profile-name>"
```

Set `WORKSPACE_PROFILE` in `.env` to that profile name, then load it again. The secret scope script reads it, and the commands below use it. If `WORKSPACE_PROFILE` already points at a different workspace, the secrets land there instead.

```bash
source scripts/load-env.sh
```

Read the endpoint IP. This command needs `RG` and `PE_NAME` in your shell. Both options have them once `.env` is loaded. If the script discovered `PE_NAME`, add it to `.env` from the `private_link.py` output and load `.env` again first:

```bash
export PE_IP=$(az network nic show --ids "$(az network private-endpoint show --resource-group "$RG" --name "$PE_NAME" --query 'networkInterfaces[0].id' -o tsv)" --query 'ipConfigurations[0].privateIPAddress' -o tsv)
echo "$PE_IP"
```

### Create the Neo4j secret scope

The notebooks read Neo4j credentials from a secret scope named `neo4j` in the workspace. Create `.env` from the sample file only if it does not exist yet, so you keep the values you set in [Before you start](#before-you-start). Then fill in the `NEO4J_*` values. The URI host is your Aura Private URI host:

```bash
[ -f .env ] || cp env.sample .env
```

`.env` is gitignored. It holds `WORKSPACE_PROFILE` and the `NEO4J_*` values. Check that `WORKSPACE_PROFILE` names the VNet-injected workspace. An exported `WORKSPACE_PROFILE` in your shell overrides the `.env` value. Then run the script from the repository root. It creates the scope and stores the `uri`, `username`, `password`, and `database` keys. The `database` key is optional, and the notebooks fall back to `neo4j` without it:

```bash
./scripts/create-secret-scope.sh
databricks --profile "$WORKSPACE_PROFILE" secrets list-secrets neo4j
```

The list must show the four keys. The script prints the profile it used, so check that it matches.

### Upload the notebooks

Run this from the repository root. It copies the notebooks into a `neo4j-privatelink-pl` folder in your user area of the workspace:

```bash
: "${WORKSPACE_PROFILE:?WORKSPACE_PROFILE is not set}"
export NOTEBOOK_DIR="/Users/$(databricks --profile "$WORKSPACE_PROFILE" current-user me -o json | jq -r .userName)/neo4j-privatelink-pl"
databricks --profile "$WORKSPACE_PROFILE" workspace mkdirs "$NOTEBOOK_DIR"
for f in pl-notebooks/0*.py; do
  databricks --profile "$WORKSPACE_PROFILE" workspace import "$NOTEBOOK_DIR/$(basename "$f" .py)" \
    --file "$f" --format SOURCE --language PYTHON --overwrite
done
```

Open the `neo4j-privatelink-pl` folder in the workspace to see the four notebooks.

### Create a classic cluster

The notebooks must run on a classic cluster, because only classic compute runs in your VNet. Serverless compute does not, so it cannot reach the private endpoint. A single-node cluster is enough. It costs money while it runs, so the command sets it to stop after 30 minutes idle.

The loader sets `SPARK_VERSION` to `16.4.x-scala2.12`, the runtime the notebooks were tested on. To use another one, set `SPARK_VERSION` in `.env` and load it again. List the Long Term Support runtime versions to check that yours is available. Then confirm the node type exists in your region:

```bash
databricks --profile "$WORKSPACE_PROFILE" clusters spark-versions -o json | jq -r '.versions[] | select(.name | test("LTS")) | select(.key | test("ml|gpu|photon|aarch64") | not) | .key' | sort -V
databricks --profile "$WORKSPACE_PROFILE" clusters list-node-types -o json | jq -r '.node_types[] | select(.node_type_id=="Standard_DS3_v2") | .node_type_id'
```

The second command must print `Standard_DS3_v2`. If it prints nothing, pick another node type from `list-node-types`.

Create the cluster and save its ID:

```bash
export CLUSTER_ID=$(databricks --profile "$WORKSPACE_PROFILE" clusters create --no-wait --json "{
  \"cluster_name\": \"pl-test\",
  \"spark_version\": \"$SPARK_VERSION\",
  \"node_type_id\": \"Standard_DS3_v2\",
  \"num_workers\": 0,
  \"autotermination_minutes\": 30,
  \"data_security_mode\": \"SINGLE_USER\",
  \"spark_conf\": {\"spark.databricks.cluster.profile\": \"singleNode\", \"spark.master\": \"local[*]\"},
  \"custom_tags\": {\"ResourceClass\": \"SingleNode\"}
}" | jq -r .cluster_id)
echo "$CLUSTER_ID"
```

Wait until the cluster is running. This takes 5 to 10 minutes. Run the second command again until it prints `RUNNING`:

```bash
databricks --profile "$WORKSPACE_PROFILE" clusters get "$CLUSTER_ID" -o json | jq -r '.state, .state_message'
```

To use the portal instead, open **Compute**, choose **Create compute**, and pick **Single node** with access mode **Single user**.

**Restart running clusters after DNS changes.** A cluster that resolved the host before the record existed can keep the public answer cached. Restart it after you create or change any A record or zone link.

### Run the validation notebook

Run the notebooks in the workspace, not from the CLI:

1. Open the workspace at `$WS_URL`, then open the `neo4j-privatelink-pl` folder and the notebook `01_validate_connectivity`.
2. In the compute dropdown at the top, attach the cluster `pl-test`. It must show as running.
3. Fill in the widgets at the top of the notebook.
   - `expected_pe_ip`: the value of `$PE_IP`.
   - `use_resolver`: `false`.
4. Click **Run all**.

[pl-notebooks/01_validate_connectivity.py](../pl-notebooks/01_validate_connectivity.py) checks four things in order:

1. The Aura host resolves to a private address. When `expected_pe_ip` is set, the answer must equal it. A public answer points to the zone link or the A record, not to an NCC rule.
2. TCP reaches the Bolt port.
3. Every routing host that Aura advertises resolves to the endpoint. A host without a record fails, and the notebook prints the `add-routing-host` command for it.
4. A Bolt query succeeds with plain DNS.

Passing means every cell finishes without an error. **The first run is expected to fail at check 3.** The routing-host records do not exist yet, and the notebook lists each host that needs one. Continue at [Add routing-host records](#add-routing-host-records).

Databricks masks the word `neo4j` in cell output as `[REDACTED]`, because the `username` and `database` secrets hold it. A host such as `p-<aura-instance-id>-<suffix>.<orch>.[REDACTED].io` really ends in `.neo4j.io`.

### Add routing-host records

Aura VDC returns Bolt routing addresses after the first connection. They look like `p-<aura-instance-id>-<suffix>.<orch>.neo4j.io`. These hosts sit under `<orch>.neo4j.io`, not under `databases.neo4j.io`. A record in the `databases.neo4j.io` zone never matches them, so each one needs its own record in a separate zone.

The `ncc-notebooks/` set maps routing hosts back to the instance host, so it never needs these records. The `pl-notebooks/` set does not, unless you turn on its `use_resolver` widget. Clients without such a resolver do need them. Examples are your own apps and jobs, `neo4j-cli`, Neo4j Desktop, and drivers elsewhere. Such a client reports `Cannot resolve address p-...neo4j.io:7687`, and the error names the host. `private_link.py verify --bolt` runs `neo4j-cli`, so it surfaces missing hosts too. `pl-notebooks/01_validate_connectivity.py` lists every advertised routing host and prints the command for each one that lacks a record. You learn the host names only after setup, from that error or from the notebook, so neither option can create these records during setup. If the notebook output shows `[REDACTED]` inside a host name, replace it with `neo4j`, as in [Run the validation notebook](#run-the-validation-notebook).

With central hub DNS, add these records in the hub as in [Add routing-host records when a client needs them](shared/private-dns-central.md#step-4-add-routing-host-records-when-a-client-needs-them).

**Script.** Run this once for each host a client reports. It creates the zone and the link the first time and adds one A record each time:

```bash
uv run scripts/private_link.py add-routing-host "p-<aura-instance-id>-<suffix>.<orch>.neo4j.io"
```

**Manual.** Set `ROUTING_HOST` in `.env` from the error message, for example `ROUTING_HOST="p-<aura-instance-id>-<suffix>.<orch>.neo4j.io"`. The loader derives the zone and the record name from it. `ORCH_ZONE` is everything after the first label, and `ROUTING_LABEL` is the first label:

```bash
source scripts/load-env.sh
echo "$ORCH_ZONE $ROUTING_LABEL"
```

The commands below also use `PE_IP` and `VNET_ID`. The loader does not set them, so read them again if you are in a new terminal:

```bash
export PE_IP=$(az network nic show --ids "$(az network private-endpoint show --resource-group "$RG" --name "$PE_NAME" --query 'networkInterfaces[0].id' -o tsv)" --query 'ipConfigurations[0].privateIPAddress' -o tsv)
export VNET_ID="$(az network vnet show --resource-group "$VNET_RG" --name "$VNET" --query id -o tsv)"
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

Repeat the record commands for every routing host a client reports. For each host, change `ROUTING_HOST` in `.env` and load it again. The loader derives `ORCH_ZONE` and `ROUTING_LABEL` anew. The zone and link are created once. A linked zone answers for the whole `<orch>.neo4j.io` domain, so any host in that domain without a record stops resolving from linked VNets.

Do not use a `p-*` record. An Azure private DNS wildcard must be the whole label `*`, so `p-*` matches nothing. A `*` record would point every host under that domain at your endpoint, so add one record per host instead.

Whether you used the script or the manual commands, restart the cluster after you add the records so it drops cached DNS answers. Then run `01_validate_connectivity` again with the same widget values. It should pass.

```bash
databricks --profile "$WORKSPACE_PROFILE" clusters restart "$CLUSTER_ID"
databricks --profile "$WORKSPACE_PROFILE" clusters get "$CLUSTER_ID" -o json | jq -r '.state'
```

Wait until the state is `RUNNING`.

### Run the other notebooks

Attach each one to the same cluster. Run them in this order after `01` passes:

1. [pl-notebooks/04_smoke_test.py](../pl-notebooks/04_smoke_test.py) repeats the DNS and routing-host checks and adds a 100-row write and read-back. It has the same `expected_pe_ip` and `use_resolver` widgets.
2. [pl-notebooks/03_push_pull_demo.py](../pl-notebooks/03_push_pull_demo.py) pushes 20 rows and pulls aggregates back. It has the `use_resolver` widget only.
3. [pl-notebooks/02_delta_to_neo4j.py](../pl-notebooks/02_delta_to_neo4j.py) round-trips a Delta table. It expects a Unity Catalog catalog named `pldemo`. Create that catalog, or change `CATALOG` near the top of the notebook to a catalog you own.

The notebooks map routing hosts back to the instance host only when you set the `use_resolver` widget to `true`. That workaround hides missing records, so use it only to compare.

### Close the public endpoint

Private Link adds a private path. It does not close the public one. After validation succeeds, disable public access in the Aura console, as in [Aura console Step 5](shared/aura-console-steps.md#step-5-disable-public-access-on-aura). Run the outside-in check from that step, then run the validation again.

Disable public access only after validation succeeds. Doing it earlier can lock you out while you debug.

### Teardown

The classic cluster keeps costing money until it stops, so delete it first. If you created the workspace with `scripts/databricks_vnet_workspace.py`, the same script removes the workspace, its two subnets, and the NSG:

```bash
databricks --profile "$WORKSPACE_PROFILE" clusters permanent-delete "$CLUSTER_ID"
uv run scripts/databricks_vnet_workspace.py down --dry-run
uv run scripts/databricks_vnet_workspace.py down
```

Then follow [Teardown: Private Link, manual](operations/teardown.md#private-link-manual) and the [Aura-side cleanup](operations/teardown.md#aura-side-cleanup-manual-no-api). Teardown removes the private endpoint and the private DNS zone. If you used the script, `uv run scripts/private_link.py destroy` removes the same Azure resources. You still remove the orphaned approval in the Aura console.

### What's next

| Topic | Guide |
|-------|-------|
| Developer laptop needs Neo4j Desktop or a browser after public access is off | [Developer desktop access](operations/developer-desktop-access.md) |
| Batch jobs, pipelines, or services in other VNets or subscriptions | [Batch jobs in other VNets](operations/batch-jobs-other-vnets.md) |
| Something does not resolve or connect | [Troubleshooting](operations/troubleshooting.md) |
| Run it in production | [Production notes](operations/production-notes.md) |
