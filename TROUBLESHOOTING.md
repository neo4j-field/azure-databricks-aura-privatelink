# Troubleshooting the NCC connection with the Databricks CLI

Use this guide when `ncc-notebooks/01_validate_connectivity.py` fails and the Aura host does not resolve from Azure Databricks serverless compute. Every check here uses only the `databricks` CLI against the account console. You do not need the account console UI, curl, or Terraform.

For other failures, see [docs/operations/troubleshooting.md](docs/operations/troubleshooting.md).

## What the symptom means

The notebook connects to the host in the `uri` secret first. Step 2 resolves that host, and step 3 opens TCP to it on port 7687. If the host prints `UNRESOLVED`, the driver has no address to connect to, so the initial connection cannot happen. The cause is almost always in the NCC setup and not in the notebook. The checks below find it.

A `p-<aura-instance-id>-*.neo4j.io` routing host that prints `UNRESOLVED` is a different case. The notebook maps those hosts to the instance host, so they do not block it. See [Neo4j driver cannot resolve p-*.neo4j.io](docs/operations/troubleshooting.md#neo4j-driver-cannot-resolve-p-neo4jio).

## Prerequisites

Set up the environment in one of two ways: [using a script](#using-a-script-to-set-up-the-environment) or [manually](#setting-the-environment-manually).

- The `databricks` CLI is installed, along with `jq`.
- You are a Databricks account admin. Workspace admin is not enough.
- You have an account-level CLI profile for `accounts.azuredatabricks.net`. A workspace profile fails with a host conflict.

The commands in this guide read five variables. Set them with the script or by hand. Either way, print them afterward to confirm that none is empty.

### Using a script to set up the environment

The repo-root `.env` holds the values, and `scripts/load-env.sh` loads them into your shell. The `.env` file comes from `env.sample`.

```bash
[ -f .env ] || cp env.sample .env
```

Edit `.env` and fill in these lines. Leave the other lines as they are:

```bash
ACCOUNT_PROFILE="azure-neo4j-account"
DATABRICKS_ACCOUNT_ID="<databricks-account-id>"
WORKSPACE_NAME="<databricks-workspace-name>"
NEO4J_URI="neo4j+s://<aura-instance-id>.databases.neo4j.io"
```

Load the file, then print the variables:

```bash
source scripts/load-env.sh
for v in ACCOUNT_PROFILE DATABRICKS_ACCOUNT_ID WORKSPACE_NAME AURA_PRIVATE_HOSTNAME; do printf '%s=%s\n' "$v" "$(printenv "$v")"; done
```

You do not set `AURA_PRIVATE_HOSTNAME` yourself. The loader derives it from the host of `NEO4J_URI` when the line is unset. Run `source scripts/load-env.sh` again after each edit to `.env`. See [Environment setup](docs/env-setup.md) for where to find each value.

### Setting the environment manually

Export the variables in your shell. The table says what each one is and where to find it:

| Variable | What it is | Where to find it | Example |
|----------|------------|------------------|---------|
| `ACCOUNT_PROFILE` | The CLI profile for `accounts.azuredatabricks.net`. It must be an account-level profile. | `databricks auth profiles` | `azure-neo4j-account` |
| `DATABRICKS_ACCOUNT_ID` | Your Databricks account ID. | Account console, top right user menu. | `<databricks-account-id>` |
| `WORKSPACE_NAME` | The name of your workspace. | Account console, Workspaces. | `dbx-aura-ncc-test` |
| `AURA_PRIVATE_HOSTNAME` | The Aura instance host. It is the host of the Private URI and the host that notebook step 1 prints after `URI scheme/host`. | Aura console, instance connection details. | `<aura-instance-id>.databases.neo4j.io` |

```bash
export ACCOUNT_PROFILE="azure-neo4j-account"
export DATABRICKS_ACCOUNT_ID="<databricks-account-id>"
export WORKSPACE_NAME="<databricks-workspace-name>"
export AURA_PRIVATE_HOSTNAME="<aura-instance-id>.databases.neo4j.io"
for v in ACCOUNT_PROFILE DATABRICKS_ACCOUNT_ID WORKSPACE_NAME AURA_PRIVATE_HOSTNAME; do printf '%s=%s\n' "$v" "$(printenv "$v")"; done
```

### Check the profile

Check that the profile works:

```bash
databricks auth profiles
databricks --profile "$ACCOUNT_PROFILE" account network-connectivity list-network-connectivity-configurations -o json | jq 'length'
```

If the second command returns an auth error, sign in again:

```bash
databricks auth login --host https://accounts.azuredatabricks.net \
  --account-id "$DATABRICKS_ACCOUNT_ID" --profile "$ACCOUNT_PROFILE"
```

## Step 1: Check that an NCC is attached to the workspace

```bash
databricks --profile "$ACCOUNT_PROFILE" account workspaces list -o json \
  | jq -r --arg n "$WORKSPACE_NAME" '.[] | select(.workspace_name == $n)
      | {workspace_id, workspace_status, network_connectivity_config_id}'
```

Read the result as follows:

| Result | Meaning | Next step |
|--------|---------|-----------|
| No output | No workspace has that name in this account. | Check the name in the account console, Workspaces. |
| `network_connectivity_config_id` is `null` | No NCC is attached. Serverless compute uses the public path. | Attach one as in [Step 2 of the NCC setup](docs/setup-ncc-manual.md#step-2-attach-the-ncc-to-the-workspace). |
| An ID is present | An NCC is attached. | Save it and continue. |

Save the NCC ID:

```bash
export NCC_ID="$(databricks --profile "$ACCOUNT_PROFILE" account workspaces list -o json \
  | jq -r --arg n "$WORKSPACE_NAME" '.[] | select(.workspace_name == $n) | .network_connectivity_config_id')"
echo "$NCC_ID"
```

## Step 2: Check the NCC region

```bash
databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  get-network-connectivity-configuration "$NCC_ID" -o json | jq '{name, region}'
```

The `region` must match the workspace region exactly. The Aura instance can be in a different region.

## Step 3: Check the private endpoint rule

```bash
databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  list-private-endpoint-rules "$NCC_ID" -o json \
  | jq '.[] | {rule_id, connection_state, domain_names, enabled, error_message, resource_id}'
```

Check each field in this order:

| Field | Expected value | If it differs |
|-------|----------------|---------------|
| `connection_state` | `ESTABLISHED` | See the state table below. |
| `domain_names` | Contains your `AURA_PRIVATE_HOSTNAME` exactly. Wildcards are not allowed. | Go to Step 4. |
| `enabled` | `null` or `true` | Run the rule update with `--enabled`, or recreate the rule. |
| `error_message` | `null` | Read the message. A visibility error needs the fix in [Fix a Private Link service visibility error](docs/setup-ncc-manual.md#fix-a-private-link-service-visibility-error). |
| `resource_id` | The Private Link service name from the Aura console | Recreate the rule with the correct value. |

If the list is empty, no rule exists. Create one as in [Step 3 of the NCC setup](docs/setup-ncc-manual.md#step-3-create-the-private-endpoint-rule).

### Connection states

| State | Meaning | Next step |
|-------|---------|-----------|
| `PENDING` | Aura has not approved the request. | Approve it in the Aura console private endpoints page. |
| `ESTABLISHED` | The private endpoint is approved and usable. | Check `domain_names`. |
| `REJECTED` or `DISCONNECTED` | Aura refused or removed the connection. | [Recreate the rule](docs/setup-ncc-manual.md#recreate-an-expired-or-failed-rule). |
| `EXPIRED` | The rule stayed `PENDING`, `REJECTED`, or `DISCONNECTED` for 14 days. | [Recreate the rule](docs/setup-ncc-manual.md#recreate-an-expired-or-failed-rule), then approve it again in Aura. |
| `CREATE_FAILED` | Databricks could not create the endpoint. | Read `error_message`, then recreate the rule. |

## Step 4: Check that the rule covers the exact host

This command prints `true` when the rule lists your host:

```bash
databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  list-private-endpoint-rules "$NCC_ID" -o json \
  | jq --arg h "$AURA_PRIVATE_HOSTNAME" 'any(.[]; (.domain_names // []) | index($h) != null)'
```

A rule can read `ESTABLISHED` and still have an empty `domain_names` list. The account console UI does not show or set this field, so only the CLI or API reveals the problem. Without the host in `domain_names`, serverless compute does not use the private DNS entry for it.

If the result is `false`, set the list. The update replaces the whole list. Include the instance host and every routing host the rule already has:

```bash
export RULE_ID="$(databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  list-private-endpoint-rules "$NCC_ID" -o json | jq -r '.[0].rule_id')"

databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  update-private-endpoint-rule "$NCC_ID" "$RULE_ID" domain_names \
  --json "{\"domain_names\": [\"${AURA_PRIVATE_HOSTNAME}\"]}"
```

The `.[0]` selector assumes one rule in the NCC. If the list has more than one rule, pick the `rule_id` from the Step 3 output yourself.

If the update is rejected, create a new rule with the full list and delete the old one. The new rule creates a new private endpoint, so approve it again in Aura. See [Add a routing hostname later](docs/setup-ncc-manual.md#add-a-routing-hostname-later).

## Step 5: Wait, then restart serverless compute

NCC changes take about 10 minutes to propagate. Sessions that started earlier keep the old DNS answer.

1. Wait 10 minutes after any attach, rule creation, or `domain_names` change.
2. Restart all serverless compute in the workspace. This includes SQL warehouses and running jobs.
3. Detach and reattach the notebook, or start a new serverless session.

## Step 6: Run the notebook again

Run `ncc-notebooks/01_validate_connectivity.py` again. If any step fails, run the first cell, **Debug DNS (optional, run first)**, and use the table below. That cell needs no other cell, so you can run it on its own. Check 3 needs `dnspython`, which the install cell adds. Run the debug cell once, and run it again after the install cell has finished to get check 3.

| Debug cell output | Meaning | What to check |
|-------------------|---------|---------------|
| Check 1 shows `<host> -> <private IP> (private)` | DNS works. | Run the notebook from the top. |
| Check 1 shows a PUBLIC IP | The rule does not cover the host, or the compute is stale. | Step 4 and Step 5. |
| Check 1 shows `UNRESOLVED` with `name not found` | The resolver answered that the name does not exist. | Step 4, and check that `AURA_PRIVATE_HOSTNAME` has no typo. |
| Check 1 shows `UNRESOLVED` with `DNS timed out or is blocked` | The resolver did not answer. | Check 2 and check 4 below, then the serverless network policy. |
| Check 2 control hosts also `UNRESOLVED` | DNS is broken or blocked for the whole compute. | The workspace network policy restricts egress. Review it in the account console. |
| Check 3 shows `NXDOMAIN` | The nameserver says the name does not exist. | Step 4. |
| Check 3 shows a CNAME chain with `privatelink` | The private DNS override applied. | Step 5, then check the TCP step. |
| Check 3 shows a timeout or `SERVFAIL` | The nameserver is unreachable or failing. | Open a Databricks support case with the full output. |
| Check 4 `TCP connect ... FAILED` | Egress to public IPs is blocked. | The serverless network policy restricts egress. Review it in the account console. |

## What to send to support

Collect this output in one message:

```bash
databricks --profile "$ACCOUNT_PROFILE" account workspaces list -o json \
  | jq --arg n "$WORKSPACE_NAME" '.[] | select(.workspace_name == $n)
      | {workspace_id, workspace_name, workspace_status, network_connectivity_config_id}'

databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  get-network-connectivity-configuration "$NCC_ID" -o json | jq '{name, region}'

databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  list-private-endpoint-rules "$NCC_ID" -o json \
  | jq '[.[] | {rule_id, connection_state, domain_names, enabled, error_message, creation_time, updated_time}]'
```

Add these items:

- The full output of every cell in `01_validate_connectivity.py`, including the debug cell.
- The Aura private endpoint state from the Aura console, `Pending` or `Approved`.
- The time of the last NCC change and the time you restarted serverless compute.
- Any workspace network policy that restricts serverless egress.
