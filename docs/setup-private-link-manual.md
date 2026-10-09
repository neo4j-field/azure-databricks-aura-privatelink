# Private Link manual setup

This guide creates the Azure side of the private path with the Azure CLI, by hand or with a script: a private endpoint in your VNet that connects to the Aura Private Link service, and the private DNS that makes the Aura hostname resolve to it. It then sets up Databricks and validates the path from a classic cluster.

Use this path when the consumer runs in your own VNet. Typical fits are classic Azure Databricks clusters, AKS, Azure Data Factory self-hosted IR, and jump VMs.

> **Is this the right guide? Your Databricks workspace must be VNet-injected.** A private endpoint lives in a VNet you own, and only compute in that VNet can use it. A workspace in a Databricks-managed VNet cannot hold a private endpoint, and serverless compute never runs in your VNet. For either, use the [NCC manual setup](setup-ncc-manual.md) instead.
>
> Check the workspace before you start. This prints the workspace VNet ID, and it prints nothing when the workspace is not VNet-injected:
>
> ```bash
> az databricks workspace show --resource-group "<workspace-rg>" --name "<workspace-name>" --query "parameters.customVirtualNetworkId.value" -o tsv
> ```
>
> No VNet-injected workspace yet? See [Classic Databricks workspaces](#classic-databricks-workspaces) to create one in a VNet you already have.

## The flow at a glance

1. [Before you start](#before-you-start): Check the prerequisites and fill in `.env`.
2. [Build the Azure private path](#build-the-azure-private-path): Create the private endpoint and the private DNS. Option A runs a script, and Option B runs the commands by hand.
3. [Set up Databricks](#databricks-setup-and-validation): Create the secret scope, upload the notebooks, and create a classic cluster.
4. [Validate](#run-the-validation-notebook): Run notebook `01`, add the routing-host records it asks for, run it again, then run `02` to `04`.
5. [Close the public endpoint](#close-the-public-endpoint) in the Aura console.
6. [Teardown](#teardown) when you no longer need the setup.

You click through the Aura console twice during step 2, whichever option you pick. Before you create the endpoint, allow-list your subscription. After you create it, accept the connection request. Neither option can do these for you, because the Aura console has no API.

## Contents

- [Before you start](#before-you-start)
- [Build the Azure private path](#build-the-azure-private-path)
  - [Option A: Run the script](#option-a-run-the-script)
  - [Option B: Run the commands yourself](#option-b-run-the-commands-yourself)
- [Databricks setup and validation](#databricks-setup-and-validation)
  - [Check the Azure side](#check-the-azure-side)
  - [Create the Neo4j secret scope](#create-the-neo4j-secret-scope)
  - [Upload the notebooks](#upload-the-notebooks)
  - [Create a classic cluster](#create-a-classic-cluster)
  - [Run the validation notebook](#run-the-validation-notebook)
  - [Add routing-host records](#add-routing-host-records)
  - [Run the remaining notebooks](#run-the-remaining-notebooks)
  - [Close the public endpoint](#close-the-public-endpoint)
  - [Teardown](#teardown)
  - [What's next](#whats-next)
- [Appendix](#appendix)
  - [Script reference](#script-reference)
  - [Classic Databricks workspaces](#classic-databricks-workspaces)
  - [Optional checks from a linked VNet](#optional-checks-from-a-linked-vnet)
  - [Recreate a rejected or disconnected endpoint](#recreate-a-rejected-or-disconnected-endpoint)
  - [Why routing hosts need their own records](#why-routing-hosts-need-their-own-records)
  - [Read the endpoint IP and VNet ID](#read-the-endpoint-ip-and-vnet-id)

## Before you start

### Prerequisites

- [ ] **Aura values:** Get them from [Aura console Steps 1 and 2](shared/aura-console-steps.md#step-1-provision-neo4j-aura-vdc-on-azure).
- [ ] **Aura allow list:** Your own Azure subscription ID must be under **Target Azure Subscription IDs** in the Aura network access configuration. Without it, the connection request never appears in Aura. Use your own subscription here, not a Databricks-managed one.
- [ ] **VNet-injected workspace:** The workspace runs in your own VNet, as in the check at the top of this guide.
- [ ] **Endpoint subnet:** The workspace VNet has a third subnet for the endpoint NIC, separate from the two Databricks subnets. The Databricks subnets are delegated, and a delegated subnet cannot hold a private endpoint. Modern subnets allow private endpoints by default.
- [ ] **Azure sign-in:** You are signed in as a user who can create private endpoints and private DNS zones in the target resource group, as in [Sign in first](env-setup.md#sign-in-first).

Custom DNS servers, tightened outbound NSG rules, or an endpoint in a different VNet each need an extra step. See [Classic Databricks workspaces](#classic-databricks-workspaces).

### Set up your environment

Both options read the same values from `.env`.

1. Copy `env.sample` to `.env` if you have not yet. `.env` is gitignored.
2. Fill in the values. [Environment setup](env-setup.md) says where to find each one, and [Which values your path needs](env-setup.md#which-values-your-path-needs) lists the ones for this path. Include the Databricks values now, so you do not have to come back: `WORKSPACE_NAME`, `WS_RG`, `WORKSPACE_URL`, `WORKSPACE_PROFILE`, and the `NEO4J_*` values. See [Databricks workspace](env-setup.md#databricks-workspace) and [Neo4j Aura](env-setup.md#neo4j-aura).
3. Load the file into your shell. Run it again after every edit to `.env`:

   ```bash
   source scripts/load-env.sh
   ```

The loader also sets `ZONE` to `databases.neo4j.io`, which the script keeps as a constant.

**Which values you must set yourself:**

- **Option A:** Option A needs only `AURA_PLS_ALIAS`. The script discovers the rest and prints what it found, so you can copy it into `.env`. See [How the script finds missing values](#how-the-script-finds-missing-values).
- **Option B:** Option B has no discovery. Set every value yourself except the ones the loader [derives](env-setup.md#what-the-loader-does).

The script reads values only from the repo-root `.env` file. It has no flags for them and ignores exported shell variables.

## Build the Azure private path

Both options build the same resources with the same names.

| | Option A: Script | Option B: Manual commands |
|---|---|---|
| How it works | `uv run scripts/private_link.py run` runs the `az` commands for you. | You copy each `az` command from this guide and run it. |
| Best for | A repeatable setup, a dry-run preview, and reruns after a pause. | Seeing each resource as you create it, using the portal, or using central hub DNS. |
| DNS modes | Single VNet only. | Single VNet or central hub. |
| Aura console steps | You do them. | You do them. |

When either option finishes, continue at [Databricks setup and validation](#databricks-setup-and-validation).

### Option A: Run the script

[`scripts/private_link.py`](../scripts/private_link.py) runs the same `az` commands as Option B. It prints each command before it runs, so the output reads as a transcript of the manual steps. Every command inspects what exists and changes only the difference, so it is safe to run again.

The script covers the single-VNet DNS mode only. For central hub DNS, use [Option B](#option-b-run-the-commands-yourself).

1. **In the Aura console:** Allow-list your subscription, as in [Step 1 of Option B](#step-1-confirm-aura-trusts-your-subscription). The script prints your subscription ID, but it cannot read the Aura allow list, so it cannot confirm that Aura trusts you.

2. Preview the run. A dry run changes nothing. It prints one row per resource with a state such as `OK`, `CREATE`, or `ERROR`, and the [dry-run states](#dry-run-states) explain each one:

   ```bash
   uv run scripts/private_link.py run --dry-run
   ```

3. Run it:

   ```bash
   uv run scripts/private_link.py run
   ```

   `run` works in three stages:

   1. It checks your Azure login, the resource group, the subnet, and the format of the PLS alias. Then it creates the private endpoint.
   2. It waits for the Aura approval. It polls every 15 seconds for up to 10 minutes.
   3. It creates the private DNS zone, the VNet link, the zone group, and the A record for the instance.

4. **In the Aura console:** Accept the connection request while `run` waits, as in [Step 3 of Option B](#step-3-approve-the-endpoint-in-aura).

5. If the approval did not happen before the timeout, the script stops with exit code `2` and prints what to click. Accept the request in the Aura console and run the same command again. This time it skips the endpoint, sees the approval, and builds the DNS.

Exit code `0` means done. For the other exit codes, the polling flags, and the single-step commands, see [Script reference](#script-reference).

### Option B: Run the commands yourself

Each step shows the Azure CLI command first. The portal alternative follows where it helps. Finish Steps 1 to 6, then continue at [Databricks setup and validation](#databricks-setup-and-validation).

#### Step 1: Confirm Aura trusts your subscription

**In the Aura console:** Open **Project settings → Security & Networking → Private endpoints**. Your subscription ID must be listed under **Target Azure Subscription IDs**, as in [Aura console Step 3](shared/aura-console-steps.md#step-3-allow-list-the-consumer-subscription).

Print the subscription ID you are signed in to, and compare it with the Aura list:

```bash
az account show --query id -o tsv
```

#### Step 2: Create the private endpoint

Look up the subnet resource ID. This works when the VNet sits in a different resource group from the endpoint:

```bash
export SUBNET_ID="$(az network vnet subnet show --resource-group "$VNET_RG" \
  --vnet-name "$VNET" --name "$PE_SUBNET" --query id -o tsv)"
echo "$SUBNET_ID"
```

Create the endpoint. It must be in the same region as the VNet. The target Private Link service can be in any region.

```bash
az network private-endpoint create \
  --name "$PE_NAME" \
  --resource-group "$PE_RG" \
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

#### Step 3: Approve the endpoint in Aura

**In the Aura console:** Approve the incoming request, as in [Aura console Step 4](shared/aura-console-steps.md#step-4-approve-the-private-endpoint-in-the-aura-console). The endpoint carries no traffic until Aura approves it.

#### Step 4: Check the connection status

Poll the connection until it reads `Approved`:

```bash
az network private-endpoint show --name "$PE_NAME" --resource-group "$PE_RG" \
  --query "manualPrivateLinkServiceConnections[0].privateLinkServiceConnectionState.{status:status, description:description}" \
  -o json
```

`Pending` means Aura has not approved the request yet. `Rejected` or `Disconnected` means you must [recreate the endpoint](#recreate-a-rejected-or-disconnected-endpoint).

Read the private IP that Azure assigned to the endpoint NIC. The DNS records point at this address:

```bash
NIC_ID="$(az network private-endpoint show --name "$PE_NAME" --resource-group "$PE_RG" \
  --query "networkInterfaces[0].id" -o tsv)"
export PE_IP="$(az network nic show --ids "$NIC_ID" \
  --query "ipConfigurations[0].privateIPAddress" -o tsv)"
echo "$PE_IP"
```

The address comes from the subnet range, for example `10.x.x.x`.

#### Step 5: Create the private DNS zone and link it

> **Central hub DNS?** If your organization runs DNS centrally in a hub VNet, do not create the zone here. Follow [Set up central hub DNS](shared/private-dns-central.md#set-up-central-hub-dns) instead. It replaces Steps 5 and 6, including the instance A record.

The endpoint gives you a private IP, but nothing yet connects the Aura hostname to it. A private DNS zone named `databases.neo4j.io` overrides the public answer inside your network. It applies only to the VNets you link, and it changes nothing for the rest of the world. See [Why databases.neo4j.io lives in your private DNS zone](shared/private-dns-central.md#why-databasesneo4jio-lives-in-your-private-dns-zone) for the reasoning.

Look up the VNet resource ID:

```bash
export VNET_ID="$(az network vnet show --resource-group "$VNET_RG" \
  --name "$VNET" --query id -o tsv)"
```

Create the zone:

```bash
az network private-dns zone create --resource-group "$PE_RG" --name "$ZONE"
```

Link it to the VNet. Auto-registration stays off because you manage the records by hand:

```bash
az network private-dns link vnet create \
  --resource-group "$PE_RG" \
  --zone-name "$ZONE" \
  --name "${PE_NAME}-vnet-link" \
  --virtual-network "$VNET_ID" \
  --registration-enabled false
```

Attach the zone to the endpoint with a zone group. The endpoint's DNS configuration then shows which zone serves it. The zone group does not create the Aura instance record. Step 6 adds that by hand:

```bash
az network private-endpoint dns-zone-group create \
  --resource-group "$PE_RG" \
  --endpoint-name "$PE_NAME" \
  --name default \
  --zone-name "$ZONE" \
  --private-dns-zone "$ZONE"
```

Each VNet whose workloads reach Aura needs its own link. Repeat the link command with a different `--name` and `--virtual-network` for every extra VNet.

#### Step 6: Add the instance A record

Map the instance ID to the endpoint private IP. The record set uses a TTL of 30 seconds. A short TTL limits how long clients keep the old address if you [recreate the endpoint](#recreate-a-rejected-or-disconnected-endpoint) and it gets a different IP:

```bash
az network private-dns record-set a create \
  --resource-group "$PE_RG" --zone-name "$ZONE" \
  --name "$AURA_INSTANCE_ID" --ttl 30

az network private-dns record-set a add-record \
  --resource-group "$PE_RG" --zone-name "$ZONE" \
  --record-set-name "$AURA_INSTANCE_ID" \
  --ipv4-address "$PE_IP"
```

Without this record, lookups from the linked VNet return NXDOMAIN and the connection fails. The Azure setup is now complete.

## Databricks setup and validation

The Azure side is done. Both options leave you with a private endpoint, an approved connection, and an instance A record. The steps below set up Databricks and prove the path works. Work through them in order:

1. [Check the Azure side](#check-the-azure-side).
2. [Create the Neo4j secret scope](#create-the-neo4j-secret-scope).
3. [Upload the notebooks](#upload-the-notebooks).
4. [Create a classic cluster](#create-a-classic-cluster).
5. [Run the validation notebook](#run-the-validation-notebook). The first run is expected to fail on routing hosts.
6. [Add routing-host records](#add-routing-host-records), restart the cluster, and run notebook `01` again.
7. [Run the remaining notebooks](#run-the-remaining-notebooks).
8. [Close the public endpoint](#close-the-public-endpoint). Do this only after validation succeeds.
9. [Teardown](#teardown) when you no longer need the setup.

If you opened a new terminal, load `.env` again with `source scripts/load-env.sh`.

### Check the Azure side

Run `verify`. It reads `PE_RG`, `PE_NAME`, and `AURA_INSTANCE_ID` from `.env`. Without flags it checks the Azure side only, so it gives the same answer from your laptop as from inside the VNet:

```bash
uv run scripts/private_link.py verify
```

It prints the connection status and the endpoint IP, then compares every A record in your private DNS zones to that IP. It checks only the zones in `$PE_RG` that carry a link named `<PE_NAME>-vnet-link` or `<PE_NAME>-orch-link`, so it covers the single-VNet mode. With central hub DNS, check with a Bolt client from a linked VNet instead, as in [Check resolution from a consuming VNet](shared/private-dns-central.md#step-5-check-resolution-from-a-consuming-vnet).

You do not need a VM to continue. Notebook `01` repeats the DNS and Bolt checks from the cluster, which is inside the VNet. To check from a VM or another machine in a linked VNet, see [Optional checks from a linked VNet](#optional-checks-from-a-linked-vnet).

### Create the Neo4j secret scope

The notebooks read Neo4j credentials from a secret scope named `neo4j` in the workspace. The script takes them from the `NEO4J_*` values in `.env`. The URI host is your Aura Private URI host.

Check that `WORKSPACE_PROFILE` in `.env` names the VNet-injected workspace. An exported `WORKSPACE_PROFILE` in your shell overrides the `.env` value. Then run the script from the repository root:

```bash
./scripts/create-secret-scope.sh
databricks --profile "$WORKSPACE_PROFILE" secrets list-secrets neo4j
```

The script creates the scope and stores the `uri`, `username`, `password`, and `database` keys. The `database` key is optional, and the notebooks fall back to `neo4j` without it. The list must show the four keys. The script prints the profile it used, so check that it matches.

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

Create a classic compute in your Databricks workspace. The notebooks must run on classic compute, because only classic compute runs in your VNet. Serverless compute does not, so it cannot reach the private endpoint.

> **Restart running clusters after DNS changes.** A cluster that resolved the host before the record existed can keep the public answer cached. Restart it after you create or change any A record or zone link.

### Run the validation notebook

Run the notebooks in the workspace, not from the CLI. Attach each one to your running classic compute and run them in number order:

| Notebook | What it checks | Widgets |
|----------|----------------|---------|
| [`01_validate_connectivity`](../pl-notebooks/01_validate_connectivity.py) | DNS, the Bolt port, routing hosts, and a Bolt query | `expected_pe_ip`, `use_resolver` |
| [`02_delta_to_neo4j`](../pl-notebooks/02_delta_to_neo4j.py) | A Delta table round trip | `use_resolver` |
| [`03_push_pull_demo`](../pl-notebooks/03_push_pull_demo.py) | A 20-row push and an aggregate pull | `use_resolver` |
| [`04_smoke_test`](../pl-notebooks/04_smoke_test.py) | The DNS and routing-host checks, then a 100-row write and read-back | `expected_pe_ip`, `use_resolver` |

Two widgets appear across the notebooks:

- **`expected_pe_ip`:** This widget takes the private endpoint IP that the A records point at. `private_link.py verify` prints it as the endpoint IP. You can also read it into `$PE_IP` as in [Read the endpoint IP and VNet ID](#read-the-endpoint-ip-and-vnet-id).
- **`use_resolver`:** Leave this widget at `false` in every notebook. Setting it to `true` maps routing hosts back to the instance host, which hides missing records, so use it only to compare.

#### Notebook 01: validate connectivity

1. Open the workspace at `$WORKSPACE_URL`, then open the `neo4j-privatelink-pl` folder and the notebook `01_validate_connectivity`.
2. In the compute dropdown at the top, attach your classic compute. It must show as running.
3. Fill in the widgets at the top of the notebook.
   - `expected_pe_ip`: the value of `$PE_IP`.
   - `use_resolver`: `false`.
4. Click **Run all**.

The notebook checks four things in order:

1. The Aura host resolves to a private address. When `expected_pe_ip` is set, the answer must equal it. A public answer points to the zone link or the A record, not to an NCC rule.
2. TCP reaches the Bolt port.
3. Every routing host that Aura advertises resolves to the endpoint. A host without a record fails, and the notebook prints the `add-routing-host` command for it.
4. A Bolt query succeeds with plain DNS.

Passing means every cell finishes without an error.

> **The first run is expected to fail at check 3.** The routing-host records do not exist yet. The notebook lists each host that needs one. Continue at [Add routing-host records](#add-routing-host-records).

Databricks masks the word `neo4j` in cell output as `[REDACTED]`, because the `username` and `database` secrets hold it. A host such as `p-<aura-instance-id>-<suffix>.<orch>.[REDACTED].io` really ends in `.neo4j.io`. Replace `[REDACTED]` with `neo4j` when you copy a host name.

### Add routing-host records

Aura VDC returns Bolt routing addresses after the first connection. They look like `p-<aura-instance-id>-<suffix>.<orch>.neo4j.io`. These hosts sit under `<orch>.neo4j.io`, not under `databases.neo4j.io`, so each one needs its own record in a separate zone. [Why routing hosts need their own records](#why-routing-hosts-need-their-own-records) explains this in more detail.

Any client without a routing-host resolver needs these records:

- The `pl-notebooks/` set, with `use_resolver` at `false`.
- Your own apps and jobs.
- `neo4j-cli`, which `private_link.py verify --bolt` runs.
- Neo4j Desktop and drivers elsewhere.

You learn the host names only after setup, so neither option can create these records during setup. They come from two places:

- `pl-notebooks/01_validate_connectivity.py` lists every advertised routing host and prints the command for each one that lacks a record.
- A client reports `Cannot resolve address p-...neo4j.io:7687`, and the error names the host.

With central hub DNS, add these records in the hub as in [Add routing-host records when a client needs them](shared/private-dns-central.md#step-4-add-routing-host-records-when-a-client-needs-them). Otherwise use the script or the manual commands below.

#### With the script

Run this once for each host. It creates the zone and the link the first time and adds one A record each time:

```bash
uv run scripts/private_link.py add-routing-host "p-<aura-instance-id>-<suffix>.<orch>.neo4j.io"
```

#### With manual commands

1. Set `ROUTING_HOST` in `.env` to the host name, for example `ROUTING_HOST="p-<aura-instance-id>-<suffix>.<orch>.neo4j.io"`. Load `.env` again. The loader derives `ORCH_ZONE`, which is everything after the first label, and `ROUTING_LABEL`, which is the first label:

   ```bash
   source scripts/load-env.sh
   echo "$ORCH_ZONE $ROUTING_LABEL"
   ```

2. The commands below also use `PE_IP` and `VNET_ID`. The loader does not set them. If you are in a new terminal, read them as in [Read the endpoint IP and VNet ID](#read-the-endpoint-ip-and-vnet-id).

3. Create the zone and link it to the same VNet. Do this once per `<orch>.neo4j.io` zone:

   ```bash
   az network private-dns zone create --resource-group "$PE_RG" --name "$ORCH_ZONE"

   az network private-dns link vnet create \
     --resource-group "$PE_RG" \
     --zone-name "$ORCH_ZONE" \
     --name "${PE_NAME}-orch-link" \
     --virtual-network "$VNET_ID" \
     --registration-enabled false
   ```

4. Add one record set and one A record for the host. The record set gets the same 30-second TTL as the instance record. Each record points at the same endpoint private IP:

   ```bash
   az network private-dns record-set a create \
     --resource-group "$PE_RG" --zone-name "$ORCH_ZONE" \
     --name "$ROUTING_LABEL" --ttl 30

   az network private-dns record-set a add-record \
     --resource-group "$PE_RG" --zone-name "$ORCH_ZONE" \
     --record-set-name "$ROUTING_LABEL" \
     --ipv4-address "$PE_IP"
   ```

5. Repeat steps 1 and 4 for every routing host. Change `ROUTING_HOST` in `.env` and load it again each time, so the loader derives `ORCH_ZONE` and `ROUTING_LABEL` anew.

> **Add every host.** A linked zone answers for the whole `<orch>.neo4j.io` domain. Any host in that domain without a record stops resolving from linked VNets.

#### Restart and run notebook 01 again

Whether you used the script or the manual commands, restart the cluster from the **Compute** page so it drops cached DNS answers. Wait until it is running. Then run `01_validate_connectivity` again with the same widget values. It should pass.

### Run the remaining notebooks

Run these after `01` passes. Attach each one to the same classic compute.

1. **`02_delta_to_neo4j`:** This notebook round-trips a Delta table. It expects a Unity Catalog catalog named `pldemo`. Create that catalog, or change `CATALOG` near the top of the notebook to a catalog you own. Set `use_resolver` to `false` and click **Run all**.
2. **`03_push_pull_demo`:** This notebook pushes 20 rows and pulls aggregates back. Set `use_resolver` to `false` and click **Run all**.
3. **`04_smoke_test`:** This notebook repeats the DNS and routing-host checks and adds a 100-row write and read-back. Set `expected_pe_ip` to the value of `$PE_IP` and `use_resolver` to `false`, then click **Run all**.

### Close the public endpoint

Private Link adds a private path. It does not close the public one.

**In the Aura console:** After validation succeeds, disable public access, as in [Aura console Step 5](shared/aura-console-steps.md#step-5-disable-public-access-on-aura). Run the outside-in check from that step, then run the validation again.

Disable public access only after validation succeeds. Doing it earlier can lock you out while you debug.

### Teardown

1. Delete the classic cluster from the **Compute** page. It keeps costing money until it stops.
2. If you created the workspace with `scripts/databricks_vnet_workspace.py`, the same script removes the workspace, its two subnets, and the NSG:

   ```bash
   uv run scripts/databricks_vnet_workspace.py down --dry-run
   uv run scripts/databricks_vnet_workspace.py down
   ```

3. Remove the private endpoint and the private DNS zone. If you used the script, `uv run scripts/private_link.py destroy` removes them. By hand, follow [Teardown: Private Link, manual](operations/teardown.md#private-link-manual).
4. **In the Aura console:** Remove the orphaned approval, as in the [Aura-side cleanup](operations/teardown.md#aura-side-cleanup-manual-no-api).

### What's next

| Topic | Guide |
|-------|-------|
| Developer laptop needs Neo4j Desktop or a browser after public access is off | [Developer desktop access](operations/developer-desktop-access.md) |
| Batch jobs, pipelines, or services in other VNets or subscriptions | [Batch jobs in other VNets](operations/batch-jobs-other-vnets.md) |
| Something does not resolve or connect | [Troubleshooting](operations/troubleshooting.md) |
| Run it in production | [Production notes](operations/production-notes.md) |

## Appendix

### Script reference

#### How the script finds missing values

With Option A you only have to set `AURA_PLS_ALIAS` in `.env`. The script discovers the rest when a value is missing, and prints what it found so you can copy it into `.env`:

| Variable | How the script finds it |
|----------|-------------------------|
| `AURA_INSTANCE_ID` | The first label of `NEO4J_URI` |
| `VNET`, `VNET_RG` | The custom VNet of a VNet-injected Databricks workspace. Set `WORKSPACE_NAME` in `.env` to pick the workspace. If you leave it unset, the script lists the VNet-injected workspaces in the subscription and asks which one. |
| `PE_RG` | The same value as `VNET_RG` |
| `PE_SUBNET` | The subnets in `VNET` that are not delegated. It asks when more than one qualifies. |
| `PE_NAME` | An existing `pe-<aura-instance-id>-*` endpoint, otherwise `pe-<aura-instance-id>-<VNet region>` |

- The script skips any workspace in a Databricks-managed VNet. Databricks creates that VNet locked in a managed resource group, and it cannot hold a private endpoint. Use the [NCC manual setup](setup-ncc-manual.md) for it.
- If the VNet you want is not a workspace VNet, set `VNET` and `VNET_RG` yourself.
- Without a terminal to ask on, the script stops and lists the options instead.

#### Dry-run states

A dry run prints one row per resource, so you can see what exists and what a real run would change:

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

#### Exit codes and polling

| Exit code | Meaning |
|-----------|---------|
| `0` | Done. |
| `1` | An error or a failed check. |
| `2` | The script paused for a step in the Aura console. |

`run` polls for the Aura approval every 15 seconds for up to 10 minutes. Change these with `--interval` and `--timeout`, both in seconds.

#### Commands

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

#### Test the script without Aura

[`scripts/private_link_testbed.py`](../scripts/private_link_testbed.py) builds a stand-in Private Link service with manual approval in a throwaway resource group. Its `up --vm` command builds the stand-in and a VM to resolve DNS from. `approve` plays the part of the Aura console. `down` deletes the resource group, and it refuses any group that lacks the testbed tag.

### Classic Databricks workspaces

A classic Databricks consumer adds a few requirements:

- **Workspace type:** The workspace must be VNet-injected. You choose VNet injection when you create the workspace.
  - A workspace in a Databricks-managed VNet cannot use this path. Use the [NCC manual setup](setup-ncc-manual.md) instead.
  - To create a VNet-injected workspace in an existing VNet, run `uv run scripts/databricks_vnet_workspace.py up --dry-run`, then `up`.
  - It creates `nsg-dbx`, the two delegated subnets `snet-dbx-host` and `snet-dbx-container`, and a Premium workspace named by `WORKSPACE_NAME` in `.env`.
  - The subnets use `10.10.10.0/24` and `10.10.11.0/24` unless you pass `--host-cidr` and `--container-cidr`. Pick ranges inside your VNet that nothing else uses.
  - The VNet needs a third subnet for the private endpoint.
- **Endpoint subnet:** `PE_SUBNET` must be a separate subnet.
  - The Databricks host and container subnets are delegated to `Microsoft.Databricks/workspaces`.
  - A delegated subnet cannot hold a private endpoint.
- **VNet:** Set `VNET` to the workspace VNet.
  - If the endpoint lives in another VNet, link the zone to the workspace VNet and peer the two VNets, as in [Batch jobs in other VNets](operations/batch-jobs-other-vnets.md).
- **Custom DNS:** If the workspace VNet uses custom DNS servers, forward `databases.neo4j.io` and any `<orch>.neo4j.io` zones to `168.63.129.16`.
  - Only that Azure resolver answers from your private DNS zones.
- **Outbound rules:** If you tightened the outbound NSG rules on the Databricks subnets, allow TCP 7687 to the endpoint subnet.

### Optional checks from a linked VNet

DNS on this path comes from the private DNS zone you linked to the VNet, not from Databricks. Only a machine in a linked VNet resolves the private address. A laptop outside the linked VNets resolves the public address, so a DNS or Bolt pass there says nothing about the private path. Run these checks from a machine in a linked VNet.

#### Run a Bolt query

Add `--bolt` to resolve the instance host, open a TCP connection to the Bolt port, and run `RETURN 1` through [`neo4j-cli`](https://github.com/neo4j/neo4j-cli) against `neo4j+s://<aura-instance-id>.databases.neo4j.io`. It reads `NEO4J_USERNAME` and `NEO4J_PASSWORD` from the repo-root `.env`:

```bash
uv run scripts/private_link.py verify --bolt
```

These checks run on the machine where you start the script. The result says whether the host resolves to a private or a public address. From a laptop outside the VNet it prints a `WARN` that it tested the public path, so a pass there cannot be mistaken for a pass on the private path.

`neo4j-cli` has no routing-host resolver. A missing routing-host record makes `--bolt` fail with `Cannot resolve address p-...neo4j.io:7687`, and the error names the host. Add a record for it as in [Add routing-host records](#add-routing-host-records).

**Manual.** Open a Bolt connection with `neo4j+s://<aura-instance-id>.databases.neo4j.io` from any Neo4j client in the linked VNet. A client without a routing-host resolver can report the same `Cannot resolve address` error.

#### Resolve from a VM in the VNet

Add `--vm <name>` to test from a VM in a linked VNet. The script uses `az vm run-command invoke`, so the VM must be running and its resource group must be one you can manage. It runs `getent hosts` for each A record and checks that the answer is the endpoint IP:

```bash
uv run scripts/private_link.py verify --vm my-jump-vm
```

If the VM is not in `PE_RG`, name its resource group:

```bash
uv run scripts/private_link.py verify --vm my-jump-vm --vm-resource-group my-vm-rg
```

Combine it with `--bolt` to also test the Bolt port from the VM. The script opens a TCP connection to port 7687 on the VM. The `RETURN 1` query still runs from the machine where you start the script, because `neo4j-cli` is not run on the VM:

```bash
uv run scripts/private_link.py verify --vm my-jump-vm --bolt
```

Classic Databricks cluster nodes are managed by Databricks, so you cannot target them with `--vm`. Use notebook `01` for the cluster.

#### Check DNS and the Bolt port by hand

Run these on a VM or pod inside a linked VNet:

```bash
nslookup "${AURA_INSTANCE_ID}.databases.neo4j.io"
nc -vz -w 5 "${AURA_INSTANCE_ID}.databases.neo4j.io" 7687
```

The `nslookup` answer must be the endpoint private IP. A public address means the VNet is not linked to the zone, or the A record is missing.

### Recreate a rejected or disconnected endpoint

This applies to both options. With the script, run `destroy`, then run `run` again. By hand, delete the endpoint, then repeat [Step 2](#step-2-create-the-private-endpoint) through [Step 4](#step-4-check-the-connection-status):

```bash
az network private-endpoint delete --name "$PE_NAME" --resource-group "$PE_RG"
```

A new endpoint can receive a different private IP. Update every A record you created, including the routing-host records, or the hostnames resolve to an address that no longer answers. The script does this for you when you run `dns` or `add-routing-host` again.

By hand, read the new `PE_IP` as in Step 4, then swap the address in each record set. Remove the old address first. `--keep-empty-record-set` keeps the record set and its 30-second TTL while it is empty:

```bash
az network private-dns record-set a remove-record \
  --resource-group "$PE_RG" --zone-name "$ZONE" \
  --record-set-name "$AURA_INSTANCE_ID" --ipv4-address "<old-ip>" \
  --keep-empty-record-set

az network private-dns record-set a add-record \
  --resource-group "$PE_RG" --zone-name "$ZONE" \
  --record-set-name "$AURA_INSTANCE_ID" --ipv4-address "$PE_IP"
```

Repeat both commands for each routing-host record. Use `--zone-name "$ORCH_ZONE"` and the routing label as the record set name.

### Why routing hosts need their own records

Routing hosts sit under `<orch>.neo4j.io`. A record in the `databases.neo4j.io` zone never matches them, so they need a zone of their own.

The `ncc-notebooks/` set maps routing hosts back to the instance host, so it never needs these records. The `pl-notebooks/` set does not map them, unless you turn on its `use_resolver` widget. That is why notebook `01` fails until the records exist.

Do not use a `p-*` record. An Azure private DNS wildcard must be the whole label `*`, so `p-*` matches nothing. A `*` record would point every host under that domain at your endpoint, so add one record per host instead.

### Read the endpoint IP and VNet ID

Several steps need `PE_IP` and `VNET_ID` in your shell. The loader does not set them. Both need `.env` loaded, and `PE_IP` needs the endpoint to exist:

```bash
: "${PE_RG:?run source scripts/load-env.sh}" "${PE_NAME:?run source scripts/load-env.sh}"
export PE_IP=$(az network nic show --ids "$(az network private-endpoint show --resource-group "$PE_RG" --name "$PE_NAME" --query 'networkInterfaces[0].id' -o tsv)" --query 'ipConfigurations[0].privateIPAddress' -o tsv)
export VNET_ID="$(az network vnet show --resource-group "$VNET_RG" --name "$VNET" --query id -o tsv)"
echo "$PE_IP $VNET_ID"
```

`uv run scripts/private_link.py verify` also prints the endpoint IP.
