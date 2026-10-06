# Manual NCC setup

This guide creates the Databricks side of the private path by hand: an NCC, a private endpoint rule for the Aura Private Link service, and the binding that attaches the NCC to your workspace. It produces the same result as the [`databricks-ncc` Terraform stack](../infra/terraform/databricks-ncc/), without Terraform or `scripts/automate.py`.

Each step shows the Databricks CLI command first. The console or REST alternative follows where one exists.

## What Terraform does for you

| Terraform resource | Manual step |
|--------------------|-------------|
| `databricks_mws_network_connectivity_config.ncc` | [Step 1: Create the NCC](#step-1-create-the-ncc) |
| `databricks_mws_ncc_binding.this` | [Step 2: Attach the NCC to the workspace](#step-2-attach-the-ncc-to-the-workspace) |
| `databricks_mws_ncc_private_endpoint_rule.aura` | [Step 3: Create the private endpoint rule](#step-3-create-the-private-endpoint-rule) |

Terraform does not wait for the rule to reach `ESTABLISHED`, restart serverless compute, or create the secret scope. The steps after Step 3 cover those.

## Before you start

Complete [README Step 0](../README.md#step-0-sign-in-and-collect-your-values) so these variables are set in your shell:

- `ACCOUNT_PROFILE`: the account-level CLI profile.
- `WORKSPACE_PROFILE`: the workspace CLI profile.
- `WORKSPACE_NAME`: the workspace display name.

Complete [README Steps 1 and 2](../README.md#step-1-provision-neo4j-aura-vdc-on-azure) in the Aura console. You need two values from them:

```bash
export AURA_PLS_ALIAS="pls-<id>.<guid>.<region>.azure.privatelinkservice"
export AURA_PRIVATE_HOSTNAME="<aura-id>.databases.neo4j.io"
export NCC_REGION="<azure-region>"
```

`NCC_REGION` must match the workspace region exactly. An example value is `eastus`.

Look up the workspace ID:

```bash
export WORKSPACE_ID="$(databricks --profile "$ACCOUNT_PROFILE" account workspaces list -o json \
  | jq -r --arg ws "$WORKSPACE_NAME" '.[] | select(.workspace_name==$ws) | .workspace_id')"
echo "$WORKSPACE_ID"
```

## Step 1: Create the NCC

The name must match `^[0-9a-zA-Z-_]{3,30}$`.

```bash
export NCC_ID="$(databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  create-network-connectivity-configuration ncc-aura-privatelink "$NCC_REGION" -o json \
  | jq -r .network_connectivity_config_id)"
echo "$NCC_ID"
```

Console alternative: [README Step 4](../README.md#step-4-create-a-network-connectivity-configuration-ncc). Read the NCC ID from the NCC page and export it as `NCC_ID`.

An account allows 10 NCCs per region. Check the existing ones before you create another:

```bash
databricks --profile "$ACCOUNT_PROFILE" account network-connectivity list-network-connectivity-configurations
```

## Step 2: Attach the NCC to the workspace

```bash
databricks --profile "$ACCOUNT_PROFILE" account workspaces update "$WORKSPACE_ID" \
  --network-connectivity-config-id "$NCC_ID"
```

The command waits for the workspace to return to `RUNNING`. Add `--no-wait` to return immediately.

Console alternative: [README Step 5](../README.md#step-5-attach-the-ncc-to-your-workspace).

A workspace holds one NCC at a time. This call replaces any NCC the workspace already has. Check the current one first:

```bash
databricks --profile "$ACCOUNT_PROFILE" account workspaces get "$WORKSPACE_ID" -o json \
  | jq -r .network_connectivity_config_id
```

Wait 10 minutes after the attach for the change to propagate.

## Step 3: Create the private endpoint rule

The console cannot create this rule, because Aura is a third-party Private Link service. The CLI sends the PLS alias as `resource_id` and the hostnames as `domain_names`. Do not set `group_id`. It is for first-party Azure resources and cannot be combined with `domain_names`.

```bash
export RULE_ID="$(databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  create-private-endpoint-rule "$NCC_ID" \
  --json "{\"resource_id\": \"${AURA_PLS_ALIAS}\", \"domain_names\": [\"${AURA_PRIVATE_HOSTNAME}\"]}" -o json \
  | jq -r .rule_id)"
echo "$RULE_ID"
```

The rule starts as `PENDING`.

### If the create call fails with a visibility error

The error text contains `ThirdPartyPrivateLinkServiceProvidedDuringPrivateEndpointCreationDoesNotExistOrIsNotVisible`. The request came from a Databricks-managed Azure subscription that the Aura allow-list does not include yet.

1. Find the subscription ID in the error. It appears in a path like `/subscriptions/<guid>/resourceGroups/prod-<region>-snp-...`.
2. In the Aura console, add that GUID to **Target Azure Subscription IDs**, as in [README Step 2](../README.md#step-2-enable-private-link-in-aura-network-access-configuration).
3. Wait about a minute, then run the create call again.

Databricks can retry from more than one managed subscription in a region. Repeat the steps for each new GUID the error shows.

### REST alternative

Use curl if the CLI is not available. The account token comes from the same `az login`.

```bash
export DATABRICKS_ACCOUNT_ID="<databricks-account-id>"
export DATABRICKS_TOKEN="$(az account get-access-token \
  --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d \
  --query accessToken -o tsv)"
BASE="https://accounts.azuredatabricks.net/api/2.0/accounts/${DATABRICKS_ACCOUNT_ID}"

# Step 1: create the NCC
curl -s -X POST "${BASE}/network-connectivity-configs" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}" -H "Content-Type: application/json" \
  --data "{\"name\": \"ncc-aura-privatelink\", \"region\": \"${NCC_REGION}\"}"

# Step 2: attach the NCC to the workspace
curl -s -X PATCH "${BASE}/workspaces/${WORKSPACE_ID}" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}" -H "Content-Type: application/json" \
  --data "{\"network_connectivity_config_id\": \"${NCC_ID}\"}"

# Step 3: create the private endpoint rule
curl -s -X POST "${BASE}/network-connectivity-configs/${NCC_ID}/private-endpoint-rules" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}" -H "Content-Type: application/json" \
  --data "{\"resource_id\": \"${AURA_PLS_ALIAS}\", \"domain_names\": [\"${AURA_PRIVATE_HOSTNAME}\"]}"
```

[`scripts/create-private-endpoint-rule.sh`](../scripts/create-private-endpoint-rule.sh) wraps the Step 3 call. It also accepts extra hostnames through `AURA_EXTRA_DOMAIN_NAMES`.

## Step 4: Approve the endpoint in Aura

Approve the incoming request in the Aura console, as in [README Step 7](../README.md#step-7-approve-the-private-endpoint-in-the-aura-console). A rule that stays `PENDING`, `REJECTED`, or `DISCONNECTED` for 14 days expires.

## Step 5: Check the rule status

Terraform has no equivalent of this step. Poll the rule until it reads `ESTABLISHED`:

```bash
databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  get-private-endpoint-rule "$NCC_ID" "$RULE_ID" -o json \
  | jq '{rule_id, connection_state, domain_names}'
```

`PENDING` means Aura has not approved the request yet. Any of `REJECTED`, `DISCONNECTED`, `EXPIRED`, or `CREATE_FAILED` means you must [recreate the rule](#recreate-an-expired-or-failed-rule).

## Step 6: Restart serverless compute and validate

Restart running SQL warehouses and serverless jobs so they pick up the NCC-managed DNS. Then create the secret scope, upload the notebooks, and run the validation notebook. All of these are in [README Step 8](../README.md#step-8-verify-dns-and-connectivity).

## Add a routing hostname later

Aura VDC can return Bolt routing addresses such as `p-<aura-id>-<suffix>.<orch>.neo4j.io`. If validation fails with `Cannot resolve address p-...neo4j.io:7687`, add that hostname to the rule. Terraform does this when you set `aura_extra_domain_names` and apply again.

The CLI update call takes the rule's full `domain_names` list, not only the new entry. The update mask is the third positional argument:

```bash
databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  update-private-endpoint-rule "$NCC_ID" "$RULE_ID" domain_names \
  --json "{\"domain_names\": [\"${AURA_PRIVATE_HOSTNAME}\", \"p-<aura-id>-<suffix>.<orch>.neo4j.io\"]}"
```

Then confirm the list with the status check from [Step 5](#step-5-check-the-rule-status) and re-run the validation notebook.

If the update is rejected, create a new rule with the full hostname list and delete the old one. The new rule creates a new private endpoint, so you must approve it again in Aura.

## Recreate an expired or failed rule

Delete the rule, then repeat [Step 3](#step-3-create-the-private-endpoint-rule) and [Step 4](#step-4-approve-the-endpoint-in-aura):

```bash
databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  delete-private-endpoint-rule "$NCC_ID" "$RULE_ID"
```

A rule in `PENDING` or `EXPIRED` is deleted at once. A rule in any other state is deactivated and removed after one day.

## Teardown

Removing the setup by hand follows [Teardown](teardown.md). A workspace cannot be left without an NCC, so teardown swaps in a placeholder NCC before it deletes the original.
