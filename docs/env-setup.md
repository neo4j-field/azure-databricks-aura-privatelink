# Environment setup

Every setup guide in this repository uses the same shell variables. This page is the one place that says what each variable is, where to find its value, and how to load all of them at once.

You keep the values in a single repo-root `.env` file. The script [`scripts/load-env.sh`](../scripts/load-env.sh) loads that file into your shell, so the commands in the guides work as written.

## Quick start

Run these from the repository root.

### Create the file

```bash
[ -f .env ] || cp env.sample .env
```

Edit `.env` and fill in the values for your path. The rest of this page says where to find each one.

### Load the file

```bash
source scripts/load-env.sh
```

The script prints how many variables it loaded. It also lists any value that still holds a `<placeholder>`. Run the same command again after each edit to `.env`.

`.env` is gitignored. Never commit real values.

## Which values your path needs

Fill in only the rows for the path you follow. The Aura and Databricks rows apply to every path.

| Path | Variables |
|------|-----------|
| **All paths** | `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`, `AURA_PLS_ALIAS`, `WORKSPACE_PROFILE` |
| **NCC, manual** | `WORKSPACE_URL`, `WORKSPACE_NAME`, `ACCOUNT_PROFILE`, `DATABRICKS_ACCOUNT_ID`, `NCC_REGION` |
| **NCC, Terraform** | `ACCOUNT_PROFILE`, `DATABRICKS_ACCOUNT_ID`. The other NCC values go in `terraform.tfvars`. |
| **Private Link, script** | Only `AURA_PLS_ALIAS` is required. The script finds the rest. |
| **Private Link, manual or Terraform commands** | `PE_RG`, `VNET`, `VNET_RG`, `PE_SUBNET`, `PE_NAME`, `REQUEST_MESSAGE`, `WORKSPACE_NAME`, `WS_RG` |
| **Central hub DNS** | `HUB_RG` |
| **Routing hosts, after setup** | `ROUTING_HOST` |

## Sign in first

Several values come from Azure, so sign in before you look anything up.

### Sign in to Azure

Use the tenant and subscription that own your Databricks workspace:

```bash
az login --tenant "<tenant-id>"
az account set --subscription "<subscription-id>"
az account show --query "{tenant:tenantId, subscription:id}" -o table
```

The tenant ID and subscription ID are not `.env` variables. The last command prints the pair you are signed in with. You can also read them in the Azure portal, under **Microsoft Entra ID** for the tenant and under **Subscriptions** for the subscription.

### Install the Databricks extension

The `az databricks` commands on this page need an Azure CLI extension. Install it once:

```bash
az extension add --name databricks
```

## Neo4j Aura

You find all of these in the [Aura console](https://console.neo4j.io). The steps that create them are in [Aura console steps](shared/aura-console-steps.md).

### Aura variables

| Variable | What it is | Where to find it |
|----------|------------|------------------|
| `NEO4J_URI` | The Bolt URI of your instance, with the `neo4j+s://` scheme. Its host is the instance host, which must match the Private URI host. | Aura console, your instance details. The **Private URI** appears after you enable Private Link in [Step 2](shared/aura-console-steps.md#step-2-enable-private-link-in-aura-network-access-configuration). |
| `NEO4J_USERNAME` | The database user. | `neo4j` unless you created another user. |
| `NEO4J_PASSWORD` | The password for that user. | Aura shows it once, when you create the instance. Use the downloaded credentials file. Reset the password in the console if you lost it. |
| `NEO4J_DATABASE` | The database name. Optional. | `neo4j` by default. |
| `AURA_PLS_ALIAS` | The Private Link service name. | Aura console, **Project settings → Security & Networking → Private endpoints**. Copy the **Private Link service name** after you save the network access configuration. It looks like `production-orch-<id>-service.<guid>.<region>.azure.privatelinkservice`. |
| `AURA_PRIVATE_HOSTNAME` | The instance host: the full host of the Private URI, such as `abcd1234.databases.neo4j.io`. | The loader takes it from `NEO4J_URI`. Set it only to override that value. |
| `AURA_INSTANCE_ID` | The instance ID: only the first label of the instance host, such as `abcd1234`. | The loader takes it from `AURA_PRIVATE_HOSTNAME`. Set it only to override that value. |
| `AURA_EXTRA_DOMAIN_NAMES` | Extra routing hosts for the NCC rule, separated by commas. Optional. | Only [`scripts/create-private-endpoint-rule.sh`](../scripts/create-private-endpoint-rule.sh) reads it. The hosts come from the driver, as in `ROUTING_HOST` below. |
| `REQUEST_MESSAGE` | The text on the Aura approval screen. Optional. | Free text. Name your team so the Aura admin can match the request. `private_link.py` uses a generic message when you leave it unset. |
| `ROUTING_HOST` | A Bolt routing host such as `p-<aura-instance-id>-<suffix>.<orch>.neo4j.io`. | You learn it only after setup. A client reports it in `Cannot resolve address p-...neo4j.io:7687`. The `pl-notebooks/01_validate_connectivity.py` notebook also lists every advertised routing host. |

### Aura host names

Three different names look alike. Each variable holds a different one, so do not swap them:

| Name | Example | Variable | Where it is used |
|------|---------|----------|------------------|
| Instance ID | `abcd1234` | `AURA_INSTANCE_ID` | The A record name in the private DNS zone, on the Private Link path |
| Instance host | `abcd1234.databases.neo4j.io` | `AURA_PRIVATE_HOSTNAME` | The host of `NEO4J_URI`, and the `domain_names` entry on the NCC path |
| Routing host | `p-abcd1234-<suffix>.<orch>.neo4j.io` | `ROUTING_HOST` | One extra DNS record or NCC domain name per host, added after setup |

On Azure the Private URI and the public URI share the same instance host. Only the DNS answer changes.

## Databricks workspace

### Workspace variables

| Variable | What it is | Where to find it |
|----------|------------|------------------|
| `WORKSPACE_NAME` | The workspace name. | Azure portal, **Azure Databricks**. Or run the list command in [Find the name and resource group](#find-the-name-and-resource-group). |
| `WS_RG` | The resource group of the workspace. This is not the managed group named `databricks-rg-<workspace>-<id>`. | Azure portal, the workspace Overview, **Resource group**. Or run the list command in [Find the name and resource group](#find-the-name-and-resource-group). |
| `WORKSPACE_URL` | The workspace URL, with `https://` in front. | Azure portal, the workspace Overview, **URL**. Or run the command in [Read the workspace URL](#read-the-workspace-url). |
| `WORKSPACE_PROFILE` | A Databricks CLI profile for the workspace. | `databricks auth profiles` lists your profiles. Create one as in [Create a workspace profile](#create-a-workspace-profile). |

### Find the name and resource group

List the workspaces in your subscription with their resource groups:

```bash
az databricks workspace list --query "[].{name:name, resourceGroup:resourceGroup, location:location}" -o table
```

Set `WORKSPACE_NAME` and `WS_RG` in `.env` to the values for your workspace.

### Read the workspace URL

Read the URL of one workspace. This needs `WS_RG` and `WORKSPACE_NAME` in your shell. Set `WORKSPACE_URL` in `.env` to the result, or export it as shown:

```bash
export WORKSPACE_URL="https://$(az databricks workspace show --resource-group "$WS_RG" --name "$WORKSPACE_NAME" --query workspaceUrl -o tsv)"
echo "$WORKSPACE_URL"
```

### Create a workspace profile

Log in to create the profile. A browser window opens for the login:

```bash
databricks auth login --host "$WORKSPACE_URL" --profile "<profile-name>"
```

Check that the profile works:

```bash
databricks auth profiles
databricks current-user me --profile "<profile-name>"
```

Set `WORKSPACE_PROFILE` in `.env` to the profile name and load it again. If the profile names a different workspace, the secret scope and the notebooks land there instead.

## Databricks account (NCC path)

The NCC calls go to `accounts.azuredatabricks.net`, which is a different auth context from the workspace. You need a separate account-level profile.

### Account variables

| Variable | What it is | Where to find it |
|----------|------------|------------------|
| `DATABRICKS_ACCOUNT_ID` | The Databricks account ID. | Account console at `accounts.azuredatabricks.net`. Open the user menu in the top right corner. |
| `ACCOUNT_PROFILE` | A Databricks CLI profile for the account. It must be an account-level profile, never a workspace profile, or the login fails with a host conflict. | Pick a name and create it as in [Create an account profile](#create-an-account-profile). |
| `NCC_REGION` | The Azure region of the workspace, not the Aura region. An NCC binds only to a workspace in its own region. | Run the command in [Read the workspace region](#read-the-workspace-region). The value looks like `eastus2`. |

### Create an account profile

Log in with the account ID to create the profile:

```bash
databricks auth login --host https://accounts.azuredatabricks.net \
  --account-id "$DATABRICKS_ACCOUNT_ID" --profile "$ACCOUNT_PROFILE"
```

Check that the profile can list NCCs:

```bash
databricks --profile "$ACCOUNT_PROFILE" account network-connectivity list-network-connectivity-configurations
```

### Read the workspace region

Read the region of the workspace and set `NCC_REGION` in `.env` to the result:

```bash
az databricks workspace show --resource-group "$WS_RG" --name "$WORKSPACE_NAME" --query location -o tsv
```

## Azure network (Private Link path)

These values describe your existing Azure network, so look them up rather than invent them. With [`scripts/private_link.py`](../scripts/private_link.py) you can leave them unset. The script finds them and prints what it found. The manual commands need every value.

### Network variables

| Variable | What it is | Where to find it |
|----------|------------|------------------|
| `VNET` | The VNet that holds the consumers. For classic Databricks, this is the workspace VNet. | Azure portal, **Virtual networks**. Or run the workspace VNet command below. |
| `VNET_RG` | The resource group that contains `VNET`. | Azure portal, the VNet Overview, **Resource group**. The loader uses `PE_RG` when you leave it unset. |
| `PE_RG` | The resource group that holds the private endpoint and the private DNS zone. | Your choice. Use `VNET_RG` unless your team keeps networking resources elsewhere. |
| `PE_SUBNET` | A subnet in `VNET` for the private endpoint. It must not be delegated. | Azure portal, the VNet, **Subnets**. Or run the subnet command below. |
| `PE_NAME` | The private endpoint name. | Your choice. The guides use `pe-<aura-instance-id>-<region>`. In Terraform, it is `private_endpoint_name`. |
| `HUB_RG` | The resource group that owns the private DNS zones. Central hub DNS only. | Your network team. If the zone already exists, run the zone command below. |
| `ZONE` | The DNS zone for instance hosts. | The loader sets `databases.neo4j.io`. Do not change it. |

### List the VNets

List the VNets and their resource groups:

```bash
az network vnet list --query "[].{vnet:name, resourceGroup:resourceGroup}" -o table
```

### Read the workspace VNet

Read the VNet of a VNet-injected workspace. The command prints nothing for a workspace in a Databricks-managed VNet, because that VNet cannot hold a private endpoint:

```bash
WS_VNET="$(az databricks workspace show --resource-group "$WS_RG" --name "$WORKSPACE_NAME" \
  --query "parameters.customVirtualNetworkId.value" -o tsv)"
echo "VNET=${WS_VNET##*/}"
echo "VNET_RG=$(echo "$WS_VNET" | cut -d/ -f5)"
```

### Pick a subnet

List the subnets in `VNET` and see which ones are delegated:

```bash
az network vnet subnet list --resource-group "$VNET_RG" --vnet-name "$VNET" \
  --query "[].{name:name, prefix:addressPrefix, delegation:delegations[0].serviceName}" -o table
```

Pick a subnet with an empty `delegation` column.

### Read the private endpoint IP

Read the private endpoint IP, which the notebooks take as the `expected_pe_ip` widget and the A records point at. It needs `PE_RG` and `PE_NAME` in your shell, and the endpoint must exist. `private_link.py verify` also prints it as `endpoint IP`:

```bash
: "${PE_RG:?run source scripts/load-env.sh}" "${PE_NAME:?run source scripts/load-env.sh}"
export PE_IP=$(az network nic show --ids "$(az network private-endpoint show --resource-group "$PE_RG" --name "$PE_NAME" --query 'networkInterfaces[0].id' -o tsv)" --query 'ipConfigurations[0].privateIPAddress' -o tsv)
echo "$PE_IP"
```

### Find the DNS zone resource group

Find the resource group of an existing `databases.neo4j.io` zone:

```bash
az network private-dns zone list --query "[?name=='databases.neo4j.io'].resourceGroup" -o tsv
```

### Match the Terraform variables

In the Terraform guide, copy these values from `terraform.tfvars`:

| `.env` variable | `terraform.tfvars` variable |
|-----------------|-----------------------------|
| `PE_RG` | `resource_group_name` |
| `VNET` | `virtual_network_name` |
| `VNET_RG` | `vnet_resource_group_name` |
| `PE_NAME` | `private_endpoint_name` |
| `AURA_INSTANCE_ID` | `aura_instance_id` |

## Values the steps set for you

The guides read these back from Azure or Databricks and export them as you go. Do not put them in `.env`.

| Variable | Set by |
|----------|--------|
| `WORKSPACE_ID` | The loader lookup, `LOAD_ENV_LOOKUP=1 source scripts/load-env.sh`, from the account workspace list |
| `NCC_ID`, `RULE_ID` | NCC manual guide, Steps 1 and 3 |
| `PLACEHOLDER_NCC_ID`, `ORIGINAL_NCC_ID` | Teardown, NCC section |
| `PE_IP`, `NIC_ID` | The Private Link guides, from the private endpoint NIC |
| `VNET_ID`, `SUBNET_ID` | The Private Link manual guide, from `az network vnet` |
| `NOTEBOOK_DIR` | The notebook upload step, from `databricks current-user me` |
| `DATABRICKS_TOKEN` | `az account get-access-token`, in the REST alternatives |

## What the loader does

`scripts/load-env.sh` works in bash and zsh. Source it. Running it as `./scripts/load-env.sh` stops with a message, because a child process cannot set variables in your shell.

### Parsing

The loader reads `KEY=VALUE` lines and never executes them. A `$` or a backtick in a password stays literal. Wrap a value in `"..."` or `'...'` when it has spaces or a `#`. A value cannot contain its own quote character. Lines that start with `#` are comments, and `export KEY=VALUE` lines work too.

### Precedence

The `.env` value replaces any value you already exported with the same name. This lets you edit `.env` and load it again. It differs from `scripts/create-secret-scope.sh` and `scripts/automate.py`, where an exported variable wins. An empty value counts as unset.

### Derived values

When a variable is unset, the loader fills it in without any network call:

| Variable | Derived from |
|----------|--------------|
| `ZONE` | The constant `databases.neo4j.io` |
| `AURA_PRIVATE_HOSTNAME` | The host of `NEO4J_URI` |
| `AURA_INSTANCE_ID` | The first label of `AURA_PRIVATE_HOSTNAME` |
| `VNET_RG` | `PE_RG` |
| `ORCH_ZONE`, `ROUTING_LABEL` | `ROUTING_HOST`, split at the first dot |

The loader records what it derived. On the next run it clears those values first, so an edited `.env` is never hidden by a stale value.

### Workspace ID lookup

`WORKSPACE_ID` is the one value the loader can read from Databricks. It is opt-in, because it makes a network call and the NCC path is the only one that needs it. Sign in to the account profile first, then run:

```bash
LOAD_ENV_LOOKUP=1 source scripts/load-env.sh
echo "$WORKSPACE_ID"
```

The lookup lists the account's workspaces with `ACCOUNT_PROFILE` and picks the one named `WORKSPACE_NAME`. It needs the `databricks` CLI and `jq`. If it cannot find the workspace, it clears `WORKSPACE_ID` and prints a warning, and the rest of `.env` still loads. A plain `source scripts/load-env.sh` makes no network call and leaves `WORKSPACE_ID` as it is. Run the lookup again after you change `WORKSPACE_NAME`.

### Placeholders

The loader warns about any value that still looks like `<placeholder>`. It does not stop, so fix the line and load again.

### Another file

Set `ENV_FILE` to load a different file:

```bash
ENV_FILE=~/envs/aura-test.env source scripts/load-env.sh
```

### Check the result

Print a few values. Do not print `NEO4J_PASSWORD`:

```bash
printenv WORKSPACE_PROFILE PE_RG VNET VNET_RG PE_SUBNET PE_NAME AURA_INSTANCE_ID
```

## Which scripts read `.env` themselves

You do not need to run the loader for these. They read `.env` on their own.

| Script | What it reads |
|--------|---------------|
| `scripts/private_link.py` | `.env` only. It ignores exported variables, so an exported `PE_RG` has no effect. |
| `scripts/databricks_vnet_workspace.py` | `.env` only, for `WORKSPACE_NAME`, `WS_RG`, and the network values. |
| `scripts/automate.py` | `.env` through `python-dotenv`. An exported variable wins. |
| `scripts/create-secret-scope.sh` | `.env`, with the same precedence as `automate.py`. |

The manual commands in the guides read your shell, so they need the loader.
