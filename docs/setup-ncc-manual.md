# NCC manual setup

This guide creates the Databricks side of the private path by hand: an NCC, a private endpoint rule for the Aura Private Link service, and the binding that attaches the NCC to your workspace. For the scripted version, see [NCC Terraform setup](setup-ncc-terraform.md).

Use this path when the consumer is **Azure Databricks Serverless**. Serverless compute lives in Databricks-managed subscriptions, so the only supported private-network path is an NCC plus a private endpoint rule. Databricks creates and manages the private endpoint and its DNS. For classic Databricks, AKS, ADF, or jump VMs, see [Private Link manual setup](setup-private-link-manual.md).

See the [NCC screenshots](../screenshots/README.md#ncc-stack-path), numbered 09 to 13, for the validated run. They show the Databricks-managed subscription IDs in the Aura allow-list, the accepted PLS alias, and the private endpoint rule in `ESTABLISHED` with its domain names.

Each step shows the Databricks CLI command first. The console or REST alternative follows where one exists.

## Steps at a glance

Do these in order. Step 4 is the only step you do in the Aura console.

1. [Step 0: Sign in and collect your values](#step-0-sign-in-and-collect-your-values)
2. [Step 1: Create the NCC](#step-1-create-the-ncc)
3. [Step 2: Attach the NCC to the workspace](#step-2-attach-the-ncc-to-the-workspace). Wait 10 minutes afterward.
4. [Step 3: Create the private endpoint rule](#step-3-create-the-private-endpoint-rule)
5. [Step 4: Approve the endpoint in Aura](#step-4-approve-the-endpoint-in-aura)
6. [Step 5: Check the rule status](#step-5-check-the-rule-status). Wait for `ESTABLISHED`.
7. [Step 6: Restart serverless compute](#step-6-restart-serverless-compute)
8. [Validate connectivity](#validate-connectivity)
9. [Close the public endpoint](#close-the-public-endpoint)
10. [Teardown](#teardown) when you no longer need the setup.

If a step fails, see [Recovery and reference](#recovery-and-reference).

## Prerequisites

Every value in this guide lives in the repo-root `.env`. [Environment setup](env-setup.md) says where to find each one and how to load them all with `source scripts/load-env.sh`.

### Databricks side

| Item | Value |
|------|-------|
| Workspace plan | **Premium** |
| Account plan | **Premium** |
| Role | **Azure Databricks account admin**. Workspace admin is not sufficient. |
| Region | Must match the NCC region |
| Serverless compute | Enabled in the workspace (`Settings → Compute → Serverless`) |

If you do not have a workspace yet, deploy an **Azure Databricks Workspace** on the **Premium** plan in your chosen region first.

### Azure side

| Item | Value |
|------|-------|
| Azure login | Signed in with `az login` as a Databricks account admin |
| Region | Region that supports Private Link for your target resources |

### Aura side

| Item | Value |
|------|-------|
| Aura tier | **AuraDB Virtual Dedicated Cloud (VDC)** or **AuraDS Enterprise** |
| Cloud | Azure |

Complete [Aura console Steps 1 and 2](shared/aura-console-steps.md) first. They give you the PLS alias and the private hostname that this guide uses.

## Step 0: Sign in and collect your values

This step signs you in and loads the values that later steps reuse. [Environment setup](env-setup.md) says where to find each value and how the loader works. Only the two sign-in values below are not in `.env`:

| Value | What it is | Where to find it |
|-------|------------|------------------|
| `TENANT_ID` | Entra ID tenant that owns the subscription your workspace is deployed in | Azure portal, Microsoft Entra ID, Overview. Or run `az account show --query tenantId -o tsv`. |
| `SUB_ID` | Azure subscription that holds the workspace | Azure portal, Subscriptions |

`az`, the `databricks` CLI, and `jq` must be on PATH. Then work through these five parts:

### 0.1 Sign in to Azure

Sign in as a Databricks account admin against the correct tenant and subscription:

```bash
az login --tenant <TENANT_ID>
az account set --subscription <SUB_ID>
```

### 0.2 Load your values

Create `.env` from the sample file if it does not exist yet. Fill in the values for the NCC path, as listed in [Which values your path needs](env-setup.md#which-values-your-path-needs). Then load the file. Run the load command again after each edit to `.env`:

```bash
[ -f .env ] || cp env.sample .env
source scripts/load-env.sh
printenv WORKSPACE_PROFILE WORKSPACE_URL WORKSPACE_NAME ACCOUNT_PROFILE DATABRICKS_ACCOUNT_ID AURA_PLS_ALIAS AURA_PRIVATE_HOSTNAME NCC_REGION
```

`AURA_PRIVATE_HOSTNAME` is the instance host, taken from `NEO4J_URI`, so the loader fills it in unless you set it yourself.

### 0.3 Check the workspace CLI profile

The profile must exist and authenticate:

```bash
databricks --profile "$WORKSPACE_PROFILE" current-user me
```

If it reports stored credentials from an older CLI version, sign in again:

```bash
databricks auth login --host "$WORKSPACE_URL" --profile "$WORKSPACE_PROFILE"
```

### 0.4 Sign in the account-console CLI profile

NCC calls target `accounts.azuredatabricks.net`, which is a different auth context from the workspace. `ACCOUNT_PROFILE` must be an account-level profile, never a workspace profile, or the login fails with a host conflict. Sign in once to create the profile:

```bash
databricks auth login --host https://accounts.azuredatabricks.net \
  --account-id "$DATABRICKS_ACCOUNT_ID" --profile "$ACCOUNT_PROFILE"
```

Then verify the profile can list NCCs:

```bash
databricks --profile "$ACCOUNT_PROFILE" account network-connectivity list-network-connectivity-configurations
```

Any JSON list counts as a pass. Check that each entry shows the `account_id` from the login command above. An auth or permission error means the profile cannot reach the account console. The NCCs listed are existing ones in the account, including expired rules and rules that belong to other people. Leave them alone, because this guide creates its own NCC.

### 0.5 Look up the workspace ID

The loader reads the ID from the account workspace list, using `ACCOUNT_PROFILE` and `WORKSPACE_NAME`:

```bash
LOAD_ENV_LOOKUP=1 source scripts/load-env.sh
echo "$WORKSPACE_ID"
```

An empty value means no workspace named `WORKSPACE_NAME` exists in this account. Check the name in Account Console, Workspaces.

## Step 1: Create the NCC

First check the limits Databricks enforces:

- An account can have at most **10 NCCs per region**.
- A region can have at most **100 private endpoints**, shared across your NCCs.
- An NCC can attach to up to **50 workspaces**.

Check the existing NCCs before you create another:

```bash
databricks --profile "$ACCOUNT_PROFILE" account network-connectivity list-network-connectivity-configurations
```

Then create the NCC. The name must match `^[0-9a-zA-Z-_]{3,30}$`.

```bash
export NCC_ID="$(databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  create-network-connectivity-configuration ncc-aura-privatelink "$NCC_REGION" -o json \
  | jq -r .network_connectivity_config_id)"
echo "$NCC_ID"
```

**Console alternative.** Sign in to the [Account Console](https://accounts.azuredatabricks.net/) as an account admin, then follow these steps:

1. Sidebar → **Security** → **Network connectivity configurations**
2. Click **Add network configuration**
3. Enter `ncc-aura-privatelink` as the **Name**. Set the **Region** to the workspace region exactly.
4. Click **Add**

Read the NCC ID from the NCC page and export it as `NCC_ID`.

## Step 2: Attach the NCC to the workspace

A workspace holds one NCC at a time. The attach call replaces any NCC the workspace already has. Check the current one first:

```bash
databricks --profile "$ACCOUNT_PROFILE" account workspaces get "$WORKSPACE_ID" -o json \
  | jq -r .network_connectivity_config_id
```

Then attach the new NCC:

```bash
databricks --profile "$ACCOUNT_PROFILE" account workspaces update "$WORKSPACE_ID" \
  --network-connectivity-config-id "$NCC_ID"
```

The command waits for the workspace to return to `RUNNING`. Add `--no-wait` to return immediately.

**Console alternative.** Attach the NCC from the workspace page in the Account Console:

1. Account Console → **Workspaces** → select your workspace
2. Click **Update workspace**
3. In **Network connectivity configurations**, select your NCC
4. Click **Update**

**Wait 10 minutes** after the attach for the change to propagate. Then **restart any running serverless services** in the workspace.

## Step 3: Create the private endpoint rule

A private endpoint rule tells Databricks to open a private connection from its serverless network to the Aura Private Link service. The rule names that service by its alias and lists the Aura hostname. Serverless compute then resolves that hostname to the private endpoint instead of the public address, so Bolt traffic stays on the Azure backbone. The rule starts as `PENDING` and becomes usable after you approve the connection in Aura in [Step 4](#step-4-approve-the-endpoint-in-aura).

> **Use the CLI, REST API, or Terraform, not the account console UI.** The console requires an Azure-native resource ID and subresource ID. Neo4j Aura is a **third-party Private Link service**, so the console flow does not apply.

The CLI sends the PLS alias as `resource_id` and the hostnames as `domain_names`. Do not set `group_id`. It is for first-party Azure resources and cannot be combined with `domain_names`.

```bash
export RULE_ID="$(databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  create-private-endpoint-rule "$NCC_ID" \
  --json "{\"resource_id\": \"${AURA_PLS_ALIAS}\", \"domain_names\": [\"${AURA_PRIVATE_HOSTNAME}\"]}" -o json \
  | jq -r .rule_id)"
echo "$RULE_ID"
```

Where:

- `AURA_PLS_ALIAS` is the Private Link service name from [Aura console Step 2](shared/aura-console-steps.md#step-2-enable-private-link-in-aura-network-access-configuration).
- `AURA_PRIVATE_HOSTNAME` is the instance host, the host of the Private URI from Aura, for example `<aura-instance-id>.databases.neo4j.io`.

The rule starts as `PENDING`.

Three related notes:

- [`scripts/create-private-endpoint-rule.sh`](../scripts/create-private-endpoint-rule.sh) wraps this call. It also accepts extra hostnames through `AURA_EXTRA_DOMAIN_NAMES`.
- To use curl instead of the CLI, see the [REST alternative](#rest-alternative).
- If the create call fails with a visibility error, see [Fix a Private Link service visibility error](#fix-a-private-link-service-visibility-error).

The repository notebooks need only `AURA_PRIVATE_HOSTNAME` in the rule. Your own jobs and apps also need each routing host. See [Add a routing hostname later](#add-a-routing-hostname-later).

## Step 4: Approve the endpoint in Aura

Approve the incoming request in the Aura console, as in [Aura console Step 4](shared/aura-console-steps.md#step-4-approve-the-private-endpoint-in-the-aura-console). Approve it promptly.

An NCC private endpoint rule that stays `PENDING`, `REJECTED`, or `DISCONNECTED` for 14 days expires and must be recreated.

## Step 5: Check the rule status

Poll the rule until it reads `ESTABLISHED`:

```bash
databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  get-private-endpoint-rule "$NCC_ID" "$RULE_ID" -o json \
  | jq '{rule_id, connection_state, domain_names, error_message}'
```

`PENDING` means Aura has not approved the request yet. Any of `REJECTED`, `DISCONNECTED`, `EXPIRED`, or `CREATE_FAILED` means you must [recreate the rule](#recreate-an-expired-or-failed-rule).

For `CREATE_FAILED`, read `error_message` first. If it shows the visibility error, follow [Fix a Private Link service visibility error](#fix-a-private-link-service-visibility-error) before you recreate the rule. Recreating without that change fails again.

## Step 6: Restart serverless compute

Restart running SQL warehouses and serverless jobs so they pick up the NCC-managed DNS. Then continue to [Validate connectivity](#validate-connectivity).

## Validate connectivity

Run these checks after the rule reads `ESTABLISHED` in [Step 5](#step-5-check-the-rule-status). The notebooks run on serverless compute and read Neo4j credentials from a secret scope.

### Create the secret scope

The notebooks read Neo4j credentials from a secret scope named `neo4j` in the workspace. Fill in the `NEO4J_*` values in the `.env` you created in [Step 0](#step-0-sign-in-and-collect-your-values). The URI host is the instance host, and it must match the one you gave the rule in [Step 3](#step-3-create-the-private-endpoint-rule). `.env` is gitignored. Then run the script from the repository root. It creates the scope and stores the `uri`, `username`, `password`, and `database` keys:

```bash
./scripts/create-secret-scope.sh
```

### Upload the notebooks

Run this from the repository root. It copies the four notebooks into `/Users/<your-user-name>/neo4j-privatelink` in the workspace. The snippet reads `WORKSPACE_PROFILE` from your shell, as loaded in [Step 0](#step-0-sign-in-and-collect-your-values):

```bash
: "${WORKSPACE_PROFILE:?WORKSPACE_PROFILE is not set}"
export NOTEBOOK_DIR="/Users/$(databricks --profile "$WORKSPACE_PROFILE" current-user me -o json | jq -r .userName)/neo4j-privatelink"
databricks --profile "$WORKSPACE_PROFILE" workspace mkdirs "$NOTEBOOK_DIR"
for f in ncc-notebooks/0*.py; do
  databricks --profile "$WORKSPACE_PROFILE" workspace import "$NOTEBOOK_DIR/$(basename "$f" .py)" \
    --file "$f" --format SOURCE --language PYTHON --overwrite
done
```

### Run the validation notebook

Open the `neo4j-privatelink` folder in the workspace. Attach each notebook to serverless compute before you run it. There are four, and you run them in number order, starting with 01:

| Notebook | What it does |
|----------|--------------|
| [`01_validate_connectivity`](../ncc-notebooks/01_validate_connectivity.py) | Checks the private path: DNS and a Bolt connection |
| [`02_delta_to_neo4j`](../ncc-notebooks/02_delta_to_neo4j.py) | A Delta table round trip |
| [`03_serverless_push_pull_demo`](../ncc-notebooks/03_serverless_push_pull_demo.py) | A small push and pull demo |
| [`04_smoke_test`](../ncc-notebooks/04_smoke_test.py) | A fuller end-to-end test with 100 sample rows |

DNS resolution for the Aura Private URI is handled by Databricks NCC because you supplied `domain_names` in [Step 3](#step-3-create-the-private-endpoint-rule). Notebook 01 checks it for you, so no separate DNS check is needed.

The notebooks install a driver resolver for the routing hosts, so they pass with only the instance host in the rule. Your own jobs and apps need the routing hosts in the rule. See [Add a routing hostname later](#add-a-routing-hostname-later).

#### Notebook 01: validate connectivity

[ncc-notebooks/01_validate_connectivity.py](../ncc-notebooks/01_validate_connectivity.py) checks the private path. It resolves the Aura host, asserts the address is private, and then runs the Bolt connectivity check. Click **Run all**.

#### Notebook 02: Delta to Neo4j

[ncc-notebooks/02_delta_to_neo4j.py](../ncc-notebooks/02_delta_to_neo4j.py) runs a Delta round trip. It reads a Delta table, writes it to Neo4j with batched `UNWIND` and `MERGE`, and writes query results back to Delta. Click **Run all**.

#### Notebook 03: serverless push and pull demo

[ncc-notebooks/03_serverless_push_pull_demo.py](../ncc-notebooks/03_serverless_push_pull_demo.py) is a small demo. It pushes a Spark DataFrame into Aura and pulls aggregates back. Click **Run all**.

#### Notebook 04: smoke test

[ncc-notebooks/04_smoke_test.py](../ncc-notebooks/04_smoke_test.py) is the fuller end-to-end test. It writes 100 sample rows, reads them back, checks the counts, and cleans up. It repeats the same DNS assertion as 01. Click **Run all**.

### Debug DNS (optional)

Run the last cell of [ncc-notebooks/01_validate_connectivity.py](../ncc-notebooks/01_validate_connectivity.py) only if a notebook fails and you need to see which hostname is not resolving privately. The cell prints each host with its IP and labels it `private` or `PUBLIC`. It prints `UNRESOLVED` when the lookup fails. It does not affect the pass or fail result of the notebook.

The cell always checks the instance host from the `uri` secret. To check a routing hostname too, add `ROUTING_HOST` to `.env` and run `./scripts/create-secret-scope.sh` again. The script stores it as the `routing_host` secret, and the cell reads it from there. The `.env` value is the full `p-<aura-instance-id>-<suffix>.<orch>.neo4j.io` hostname named in a client's error.

The notebooks map `p-*.neo4j.io` routing hostnames to the instance host, so they never report an unresolved one. This cell checks a routing hostname directly.

If a hostname resolves to a public IP or not at all, see [Troubleshooting](operations/troubleshooting.md).

## Close the public endpoint

The NCC rule adds a private path. It does not close the public one. After validation succeeds, disable public access in the Aura console, as in [Aura console Step 5](shared/aura-console-steps.md#step-5-disable-public-access-on-aura). Run the outside-in check from that step, then run the validation again.

Disable public access only after validation succeeds. Doing it earlier can lock you out while you debug.

## Teardown

Follow [Teardown: NCC](operations/teardown.md#ncc-databricks-serverless), then the [Aura-side cleanup](operations/teardown.md#aura-side-cleanup-manual-no-api). A workspace cannot be left without an NCC, so teardown swaps in a placeholder NCC before it deletes the original.

## Recovery and reference

Come here only when a step above points you to one of these sections.

### Add a routing hostname later

Aura VDC can return Bolt routing addresses such as `p-<aura-instance-id>-<suffix>.<orch>.neo4j.io`. The repository notebooks install a driver resolver that maps these hosts back to the instance host, so they pass without this step. A client without such a resolver fails with `Cannot resolve address p-...neo4j.io:7687`. Your own jobs and apps fall in this group. The error names the host. Add that host to the rule's `domain_names` by its full name.

The CLI update call replaces the rule's whole `domain_names` list. Include the instance host and every routing host, not only the new entry. The update mask is the third positional argument:

```bash
databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  update-private-endpoint-rule "$NCC_ID" "$RULE_ID" domain_names \
  --json "{\"domain_names\": [\"${AURA_PRIVATE_HOSTNAME}\", \"p-<aura-instance-id>-<suffix>.<orch>.neo4j.io\"]}"
```

Add one entry to the list for each routing host. Then confirm the list with the status check from [Step 5](#step-5-check-the-rule-status) and re-run the client that reported the error.

If the update is rejected, create a new rule with the full hostname list and delete the old one. The new rule creates a new private endpoint, so you must approve it again in Aura.

### Fix a Private Link service visibility error

The create call in [Step 3](#step-3-create-the-private-endpoint-rule) can fail with a visibility error. The error text contains `ThirdPartyPrivateLinkServiceProvidedDuringPrivateEndpointCreationDoesNotExistOrIsNotVisible`. The request came from a Databricks-managed Azure subscription that the Aura allow-list does not include yet.

1. Find the subscription ID in the error. It appears in a path like `/subscriptions/<guid>/resourceGroups/prod-<region>-snp-...`. Add only a GUID from your own failed call. Do not add subscription IDs from other sources.
2. Add that GUID to **Target Azure Subscription IDs** in Aura, as in [Aura console Step 3](shared/aura-console-steps.md#step-3-allow-list-the-consumer-subscription).
3. Wait about a minute, then run the create call again.

Databricks can retry from more than one managed subscription in a region. Repeat the steps for each new GUID the error shows.

Databricks provisions the private endpoint asynchronously. The create call can therefore succeed and the rule can later read `CREATE_FAILED` in [Step 5](#step-5-check-the-rule-status). Treat that the same way. Read the rule's `error_message`. If it names the visibility error, allow-list the managed subscription, delete the failed rule, and create it again.

### Recreate an expired or failed rule

Delete the rule, then repeat [Step 3](#step-3-create-the-private-endpoint-rule) and [Step 4](#step-4-approve-the-endpoint-in-aura):

```bash
databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  delete-private-endpoint-rule "$NCC_ID" "$RULE_ID"
```

A rule in `PENDING` or `EXPIRED` is deleted at once. A rule in any other state is deactivated and removed after one day.

### REST alternative

Use curl if the CLI is not available. The account token comes from the same `az login`.

It reuses `DATABRICKS_ACCOUNT_ID` and the other values from [Step 0](#step-0-sign-in-and-collect-your-values).

```bash
export DATABRICKS_TOKEN="$(az account get-access-token \
  --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d \
  --query accessToken -o tsv)"
BASE="https://accounts.azuredatabricks.net/api/2.0/accounts/${DATABRICKS_ACCOUNT_ID}"

# Step 1: create the NCC
NCC_RESPONSE="$(curl -s -X POST "${BASE}/network-connectivity-configs" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}" -H "Content-Type: application/json" \
  --data "{\"name\": \"ncc-aura-privatelink\", \"region\": \"${NCC_REGION}\"}")"
echo "$NCC_RESPONSE" | jq .
export NCC_ID="$(echo "$NCC_RESPONSE" | jq -r '.network_connectivity_config_id // empty')"
: "${NCC_ID:?NCC create failed. See the response above.}"

# Step 2: attach the NCC to the workspace
curl -s -X PATCH "${BASE}/workspaces/${WORKSPACE_ID}" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}" -H "Content-Type: application/json" \
  --data "{\"network_connectivity_config_id\": \"${NCC_ID}\"}" \
  | jq '{error_code, message, workspace_status, network_connectivity_config_id}'

# Step 3: create the private endpoint rule
RULE_RESPONSE="$(curl -s -X POST "${BASE}/network-connectivity-configs/${NCC_ID}/private-endpoint-rules" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}" -H "Content-Type: application/json" \
  --data "{\"resource_id\": \"${AURA_PLS_ALIAS}\", \"domain_names\": [\"${AURA_PRIVATE_HOSTNAME}\"]}")"
echo "$RULE_RESPONSE" | jq .
export RULE_ID="$(echo "$RULE_RESPONSE" | jq -r '.rule_id // empty')"
: "${RULE_ID:?Rule create failed. See the response above.}"
```

Each call prints its response, and the guard lines stop the script from carrying an empty ID into the next call. A failed attach shows `error_code` and `message`. A successful one shows the NCC ID on the workspace.

### What the NCC does not cover

The NCC adds a private path to Aura. It does not block other outbound traffic from serverless compute. A serverless egress policy in restricted access mode does that, and this guide does not create one. If you add one, allow the Aura hostnames and the hosts your notebooks need, such as PyPI for `%pip install`.

## What's next

| Topic | Guide |
|-------|-------|
| Developer laptop needs Neo4j Desktop or a browser after public access is off | [Developer desktop access](operations/developer-desktop-access.md) |
| Batch jobs, pipelines, or services in other VNets or subscriptions | [Batch jobs in other VNets](operations/batch-jobs-other-vnets.md) |
| Something does not resolve or connect | [Troubleshooting](operations/troubleshooting.md) |
| Run it in production | [Production notes](operations/production-notes.md) |
