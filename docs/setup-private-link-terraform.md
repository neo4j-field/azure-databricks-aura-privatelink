# Private Link Terraform setup

This guide uses Terraform to create a private endpoint in your VNet that connects to the Aura Private Link service. It also creates the private DNS that makes the Aura hostname resolve to the endpoint NIC. To do the same steps by hand with the Azure CLI, see [Private Link manual setup](setup-private-link-manual.md). Complete [Aura console Steps 1 to 3](shared/aura-console-steps.md) first.

Use this stack, the **Private Endpoint stack**, when your consumer is **not** Databricks Serverless. Typical fits:

- Classic Azure Databricks clusters in VNet-injected workspaces
- Azure Data Factory self-hosted IR
- AKS workloads
- Jump VMs and Bastion-fronted admin hosts
- Azure Functions on VNet integration

For **Azure Databricks Serverless**, use the **NCC stack** [`infra/terraform/databricks-ncc/`](../infra/terraform/databricks-ncc/) instead, as described in [NCC Terraform setup](setup-ncc-terraform.md). Serverless compute runs in Databricks-managed subscriptions and cannot consume a customer-VNet private endpoint.

In the Private Endpoint stack, you create the endpoint in your own VNet, so you also own DNS. The Terraform for this stack lives in [`infra/terraform/azure-private-endpoint/`](../infra/terraform/azure-private-endpoint/).

This stack has been validated end-to-end from a Windows VM in East US to an Aura instance in UK South. See [`screenshots/`](../screenshots/) for the captured walkthrough. It includes the Aura-side approval, the `Disable public traffic` lockdown, the VM `nslookup` resolving to the PE NIC, and a working Neo4j Browser session over the private path.

## What this stack creates

| Resource | Purpose |
|---|---|
| `azurerm_private_endpoint` | The PE NIC inside your subnet that connects to the Aura PLS through its alias |
| `azurerm_private_dns_zone` | The `databases.neo4j.io` private DNS zone for the Aura hostname |
| `azurerm_private_dns_zone_virtual_network_link` | Links the zone to your VNet so resolvers in that VNet see it |
| `azurerm_private_dns_a_record` | Maps `<aura-instance-id>.databases.neo4j.io` to the PE NIC IP |

With `manage_private_dns = false`, the stack creates only the private endpoint. See [Private DNS modes](#private-dns-modes).

## Prerequisites

- Terraform >= 1.6.0
- An existing VNet and a subnet that will host the PE NIC. The subnet must allow private endpoints, which is the default for modern subnets.
- The subscription you deploy into is **already registered** in the Aura console under **Target Azure Subscription IDs**, as in [Aura console Step 3](shared/aura-console-steps.md#step-3-allow-list-the-consumer-subscription). Paste your own subscription IDs there, not a Databricks-managed one. Without this, the connection request never appears in Aura for approval.
- Azure credentials with permission to create private endpoints and private DNS zones in the target resource group. A CLI login, environment variables, or a managed identity all work.
- For a classic Databricks consumer, the workspace also meets the [classic Databricks requirements](setup-private-link-manual.md#classic-databricks-workspaces) in the manual guide.

### Find your Azure subscription ID

The Aura network access configuration asks for the Azure subscription ID where the private endpoint will be created. Look it up before you start:

- **Azure portal:** Open **Subscriptions**, select your subscription, and copy the **Subscription ID** from the Overview page.
- **Azure CLI:** Run `az account show --query id -o tsv` for the active subscription. Run `az account list --query "[].{name:name, id:id}" -o table` to list all of them.

## Definitions

- **Private endpoint:** A private endpoint is a network interface that Azure places inside one of your subnets. It holds a private IP and forwards traffic across the Azure backbone to the Aura Private Link service. Aura traffic therefore stays off the public internet. This guide shortens it to PE.
- **PE NIC:** The PE NIC is the network interface card of the private endpoint. It is the object that owns the private IP.
- **Private IP:** The private IP is the address that Azure assigns to the PE NIC from your VNet address range, such as `10.x.x.x`. Every mention of "the private IP" or "the PE private IP" in this guide means this one address. The Aura hostname must resolve to it for private connectivity to work.
- **PE subnet:** The PE subnet is the subnet that hosts the PE NIC. Its address range determines which private IP the endpoint receives.
- **Private Link service:** The Private Link service is the Aura-side service that your private endpoint connects to. Neo4j publishes it as an alias, and you approve the connection in the Aura console. This guide shortens it to PLS.
- **Private DNS zone:** A private DNS zone is an Azure resource that answers DNS queries for one domain name, but only for the VNets you link to it. It overrides public DNS for that name inside your network.
- **VNet:** A VNet is an Azure Virtual Network.
- **Hub and spoke:** Hub and spoke is a topology where one central VNet, the hub, owns shared services such as DNS. Workload VNets, the spokes, connect to the hub over peering.

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

1. Approve the incoming endpoint in the Aura console, as in [Aura console Step 4](shared/aura-console-steps.md#step-4-approve-the-private-endpoint-in-the-aura-console).
2. If you set `manage_private_dns = false`, do [Set up central hub DNS](shared/private-dns-central.md#set-up-central-hub-dns) before validation. The stack created no DNS records in that mode.
3. Continue to [Validate connectivity](#validate-connectivity).

## Notes

- The `is_manual_connection = true` flag is required for a cross-subscription PLS like Aura. The consumer side cannot auto-approve the connection.
- `private_connection_resource_alias` is the canonical handle that Aura publishes. The Aura PLS has no ARM resource ID on your side, so do not try to use one.

## Private DNS modes

The `manage_private_dns` variable decides who owns the `databases.neo4j.io` private DNS zone. [Why databases.neo4j.io lives in your private DNS zone](shared/private-dns-central.md#why-databasesneo4jio-lives-in-your-private-dns-zone) explains why the zone carries the Neo4j domain name.

- **`manage_private_dns = true`:** This default builds the single-VNet mode. Terraform creates the zone, links it to your VNet, adds the instance A record, and attaches a `private_dns_zone_group` to the endpoint. It creates no routing-host records.
- **`manage_private_dns = false`:** This setting is for central hub DNS. Terraform creates only the private endpoint. It skips the zone, the VNet link, the A record, and the zone group, as [`main.tf`](../infra/terraform/azure-private-endpoint/main.tf) shows. You then add the records in the hub as in [Set up central hub DNS](shared/private-dns-central.md#set-up-central-hub-dns).

To choose between the modes, see [Self-managed vs. central DNS](shared/private-dns-central.md#self-managed-vs-central-dns).

## Routing-host records

Aura VDC returns Bolt routing addresses after the first connection. They look like `p-<aura-instance-id>-<suffix>.<orch>.neo4j.io`, where `<orch>` is a label such as `production-orch-<id>`. These hosts sit under `<orch>.neo4j.io`, not under `databases.neo4j.io`. A record in the `databases.neo4j.io` zone never matches them, so each one needs its own record in a separate zone. This stack does not create these records.

The `ncc-notebooks/` set maps routing hosts back to the instance host, so it never needs these records. The `pl-notebooks/` set does not, unless you turn on its `use_resolver` widget. Clients without such a resolver do need them. Examples are your own apps and jobs, `neo4j-cli`, Neo4j Desktop, and drivers elsewhere. Such a client reports `Cannot resolve address p-...neo4j.io:7687`, and the error names the host. `private_link.py verify --bolt` runs `neo4j-cli`, so it surfaces missing hosts too. `pl-notebooks/01_validate_connectivity.py` lists every advertised routing host and prints the command for each one that lacks a record.

The steps below cover the single-VNet mode. With central hub DNS, add the records in the hub as in [Add routing-host records when a client needs them](shared/private-dns-central.md#step-4-add-routing-host-records-when-a-client-needs-them).

Set the values from `terraform.tfvars`. Run the commands in this section from `infra/terraform/azure-private-endpoint`:

```bash
export RG="<resource-group>"              # resource_group_name
export VNET="<vnet-name>"                 # virtual_network_name
export VNET_RG="<vnet-resource-group>"    # vnet_resource_group_name, or the same value as RG
export PE_NAME="<private-endpoint-name>"  # private_endpoint_name
```

**Script.** The script reads `RG`, `VNET`, `VNET_RG`, `PE_NAME`, and `AURA_INSTANCE_ID` from the repo-root `.env`, not from the variables above. Add the same values there. Run this once for each host a client reports. It creates the zone and the link the first time and adds one A record each time:

```bash
uv run ../../../scripts/private_link.py add-routing-host "p-<aura-instance-id>-<suffix>.<orch>.neo4j.io"
```

**Manual.** Set the routing host from the error message, then derive the zone and the record name from it. Read the endpoint IP and the VNet ID as well:

```bash
export ROUTING_HOST="p-<aura-instance-id>-<suffix>.<orch>.neo4j.io"
export ORCH_ZONE="${ROUTING_HOST#*.}"       # everything after the first label
export ROUTING_LABEL="${ROUTING_HOST%%.*}"  # the first label
export PE_IP="$(terraform output -raw private_endpoint_nic_ip)"
export VNET_ID="$(az network vnet show --resource-group "$VNET_RG" \
  --name "$VNET" --query id -o tsv)"
```

Create the zone and link it to the VNet. The link name `${PE_NAME}-orch-link` matches the script, so `verify` checks this zone:

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

## Validate connectivity

DNS on this path comes from the private DNS zone you linked to the VNet, not from Databricks. Run every check from a VM, cluster, or pod inside a linked VNet. A machine outside the linked VNets resolves the public address, so a pass there says nothing about the private path.

Set the values from `terraform.tfvars`. The checks below and the script read them:

```bash
export RG="<resource-group>"                  # resource_group_name
export PE_NAME="<private-endpoint-name>"      # private_endpoint_name
export AURA_INSTANCE_ID="<aura-instance-id>"  # aura_instance_id
```

### Check DNS and the Bolt port

```bash
nslookup "${AURA_INSTANCE_ID}.databases.neo4j.io"
nc -vz -w 5 "${AURA_INSTANCE_ID}.databases.neo4j.io" 7687
```

The `nslookup` answer must be the endpoint private IP. A public address means the VNet is not linked to the zone, or the A record is missing.

### Run a Bolt query

**Script.** [`scripts/private_link.py`](../scripts/private_link.py) runs the checks for you. It reads `RG`, `PE_NAME`, and `AURA_INSTANCE_ID` from the repo-root `.env`, so add the same three values there. Run it from the repository root:

```bash
uv run scripts/private_link.py verify --bolt
```

`verify` compares every A record to the endpoint IP. It checks only the zones in `$RG` that carry a link named `<PE_NAME>-vnet-link` or `<PE_NAME>-orch-link`. It therefore covers the single-VNet mode, which is this stack with `manage_private_dns = true`. With central hub DNS, check with `nslookup` from a linked VNet and a Bolt client instead, as in [Check resolution from a consuming VNet](shared/private-dns-central.md#step-5-check-resolution-from-a-consuming-vnet).

Add `--vm <name>` to resolve each host from a VM in the VNet. `--bolt` runs `RETURN 1` through [`neo4j-cli`](https://github.com/neo4j/neo4j-cli) against `neo4j+s://<aura-instance-id>.databases.neo4j.io`. Pass `--bolt-credential <name>` or `--bolt-env <file>` for the login. The check reports whether the host resolves to a private or a public address. A pass from a laptop therefore cannot be mistaken for a pass on the private path.

`neo4j-cli` has no routing-host resolver. A missing routing-host record makes `--bolt` fail with `Cannot resolve address p-...neo4j.io:7687`, and the error names the host. Add a record for it as in [Routing-host records](#routing-host-records).

**Manual.** Open a Bolt connection with `neo4j+s://<aura-instance-id>.databases.neo4j.io` from any Neo4j client in the linked VNet. A client without a routing-host resolver can report `Cannot resolve address p-...neo4j.io:7687`. The error names the host. Add a record for it as in [Routing-host records](#routing-host-records).

### Classic Databricks clusters

The notebooks in `pl-notebooks/` run on classic clusters in a VNet-injected workspace, as long as the workspace VNet is linked to the private DNS zone.

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
for f in pl-notebooks/0*.py; do
  databricks --profile "$WORKSPACE_PROFILE" workspace import "$NOTEBOOK_DIR/$(basename "$f" .py)" \
    --file "$f" --format SOURCE --language PYTHON --overwrite
done
```

Open the `neo4j-privatelink` folder in the workspace and attach each notebook to a classic cluster instead of serverless compute.

**Restart running clusters after DNS changes.** A cluster that resolved the host before the record existed can keep the public answer cached. Restart it after you create or change any A record or zone link.

Run [pl-notebooks/01_validate_connectivity.py](../pl-notebooks/01_validate_connectivity.py). It checks four things in order:

1. The Aura host resolves to a private address. Set the `expected_pe_ip` widget to the endpoint IP to pin the exact address. A public answer points to the zone link or the A record, not to an NCC rule.
2. TCP reaches the Bolt port.
3. Every routing host that Aura advertises resolves to the endpoint. A host without a record fails, and the notebook prints the `add-routing-host` command for it.
4. A Bolt query succeeds with plain DNS.

The notebook maps routing hosts back to the instance host only when you set the `use_resolver` widget to `true`. That workaround hides missing records, so use it only to compare. [pl-notebooks/04_smoke_test.py](../pl-notebooks/04_smoke_test.py) repeats the DNS and routing-host checks and adds a 100-row write and read-back.

## Close the public endpoint

Private Link adds a private path. It does not close the public one. After validation succeeds, disable public access in the Aura console, as in [Aura console Step 5](shared/aura-console-steps.md#step-5-disable-public-access-on-aura). Run the outside-in check from that step, then run the validation again.

Disable public access only after validation succeeds. Doing it earlier can lock you out while you debug.

## Teardown

Follow [Teardown: Private Link, Terraform](operations/teardown.md#private-link-terraform), then the [Aura-side cleanup](operations/teardown.md#aura-side-cleanup-manual-no-api). `terraform destroy` does not remove routing-host records you added by hand. It also leaves any records you added in a hub zone.

## What's next

| Topic | Guide |
|-------|-------|
| Developer laptop needs Neo4j Desktop or a browser after public access is off | [Developer desktop access](operations/developer-desktop-access.md) |
| Batch jobs, pipelines, or services in other VNets or subscriptions | [Batch jobs in other VNets](operations/batch-jobs-other-vnets.md) |
| Something does not resolve or connect | [Troubleshooting](operations/troubleshooting.md) |
| Run it in production | [Production notes](operations/production-notes.md) |
