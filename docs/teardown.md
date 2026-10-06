# Teardown

Removing the Databricks side is mostly `terraform destroy` on the `databricks-ncc` stack, plus one manual step that has no clean automation and two Aura-console actions that have no API.

**The NCC gotcha.** `terraform destroy` deletes the private endpoint rule and the workspace binding, but it **cannot delete the NCC itself**. The NCC is attached to the workspace through the workspace's own `network_connectivity_config_id`, and the Databricks account API has no way to *unset* an NCC on a workspace. It can only be **swapped for a different one**. So destroy fails on the final resource with:

```
cannot delete mws network connectivity config: ... unable to be deleted because
it is attached to one or more workspaces: <workspace-id>
```

That error is expected. To finish teardown, reassign the workspace to a throwaway placeholder NCC, then delete the original.

## Step 1: Destroy the stack

```bash
terraform -chdir=infra/terraform/databricks-ncc destroy
```

This removes the private endpoint rule and the workspace binding, then fails on the NCC with the message above. Note the NCC id (also available via `terraform -chdir=infra/terraform/databricks-ncc output ncc_id`).

## Step 2: Detach the NCC by swapping in a placeholder

The workspace must always point at *some* NCC, so free the original by pointing the workspace at an empty placeholder instead. The placeholder's region **must match the workspace region**. An NCC only binds to a workspace in its own region.

**Account console:** Workspaces → your workspace → **Update workspace** → under **Network connectivity configurations** pick a different NCC (create an empty one first if you have none) → **Update**.

**REST API** (mirrors [Step 6](../README.md#step-6-add-a-private-endpoint-rule-for-neo4j-aura-pls) of the README; `DATABRICKS_ACCOUNT_ID`, `WORKSPACE_ID`, and a `DATABRICKS_TOKEN` bearer for `accounts.azuredatabricks.net`):

```bash
BASE="https://accounts.azuredatabricks.net/api/2.0/accounts/${DATABRICKS_ACCOUNT_ID}"

# a. Create an empty placeholder NCC in the workspace's region.
PLACEHOLDER=$(curl -s -X POST "${BASE}/network-connectivity-configs" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}" -H "Content-Type: application/json" \
  --data '{"name":"ncc-placeholder","region":"eastus"}' \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["network_connectivity_config_id"])')

# b. Swap the workspace onto the placeholder. This frees the original NCC.
curl -s -X PATCH "${BASE}/workspaces/${WORKSPACE_ID}" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}" -H "Content-Type: application/json" \
  --data "{\"network_connectivity_config_id\": \"${PLACEHOLDER}\"}"
```

## Step 3: Delete the original NCC and reconcile Terraform

```bash
# ORIGINAL_NCC_ID is the NCC id from Step 1.
curl -s -X DELETE "${BASE}/network-connectivity-configs/${ORIGINAL_NCC_ID}" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}"

terraform -chdir=infra/terraform/databricks-ncc state rm \
  databricks_mws_network_connectivity_config.ncc
```

The `state rm` drops the already-deleted NCC from Terraform state so the stack reads clean. The placeholder NCC stays attached to the workspace; it is empty and harmless; leave it or delete it later from the console.

## Step 4: Aura-side cleanup (manual, no API)

1. Open the Aura private endpoints page, as described in [README Step 7](../README.md#step-7-approve-the-private-endpoint-in-the-aura-console). Remove the now-orphaned private endpoint approval.
2. Optionally remove the Databricks-managed subscription from **Target Azure Subscription IDs** if nothing else uses it.

The `neo4j` secret scope and the imported validation notebook remain in the workspace; delete them by hand for a full reset.
