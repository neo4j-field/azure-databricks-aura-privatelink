# Teardown

Pick the section for the path you used, then finish with the [Aura-side cleanup](#aura-side-cleanup-manual-no-api). The Aura console has no API, so that cleanup is always manual.

| Path | Section |
|------|---------|
| NCC, Terraform or manual | [NCC (Databricks Serverless)](#ncc-databricks-serverless) |
| Private Link, manual | [Private Link, manual](#private-link-manual) |
| Private Link, Terraform | [Private Link, Terraform](#private-link-terraform) |

## NCC (Databricks Serverless)

Removing the Databricks side is mostly `terraform destroy` on the `databricks-ncc` stack, plus one manual step that has no clean automation.

**The NCC gotcha.** `terraform destroy` deletes the private endpoint rule and the workspace binding, but it **cannot delete the NCC itself**. The NCC is attached to the workspace through the workspace's own `network_connectivity_config_id`, and the Databricks account API has no way to *unset* an NCC on a workspace. It can only be **swapped for a different one**. So destroy fails on the final resource with:

```
cannot delete mws network connectivity config: ... unable to be deleted because
it is attached to one or more workspaces: <workspace-id>
```

That error is expected. To finish teardown, reassign the workspace to a throwaway placeholder NCC, then delete the original.

If you built the setup by hand, skip Step 1. Delete the rule with the CLI command in [Recreate an expired or failed rule](../setup-ncc-manual.md#recreate-an-expired-or-failed-rule), then continue at Step 2.

### Step 1: Destroy the stack

```bash
terraform -chdir=infra/terraform/databricks-ncc destroy
```

This removes the private endpoint rule and the workspace binding, then fails on the NCC with the message above. Note the NCC id. It is also available via `terraform -chdir=infra/terraform/databricks-ncc output ncc_id`.

### Step 2: Detach the NCC by swapping in a placeholder

The workspace must always point at *some* NCC, so free the original by pointing the workspace at an empty placeholder instead. The placeholder's region **must match the workspace region**. An NCC only binds to a workspace in its own region.

**Account console:** Workspaces → your workspace → **Update workspace** → under **Network connectivity configurations** pick a different NCC (create an empty one first if you have none) → **Update**.

**REST API** (mirrors [Step 3 of the manual NCC guide](../setup-ncc-manual.md#step-3-create-the-private-endpoint-rule); `DATABRICKS_ACCOUNT_ID`, `WORKSPACE_ID`, and a `DATABRICKS_TOKEN` bearer for `accounts.azuredatabricks.net`):

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

### Step 3: Delete the original NCC and reconcile Terraform

```bash
# ORIGINAL_NCC_ID is the NCC id from Step 1.
curl -s -X DELETE "${BASE}/network-connectivity-configs/${ORIGINAL_NCC_ID}" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}"

terraform -chdir=infra/terraform/databricks-ncc state rm \
  databricks_mws_network_connectivity_config.ncc
```

The `state rm` drops the already-deleted NCC from Terraform state so the stack reads clean. Skip it if you built the setup by hand. The placeholder NCC stays attached to the workspace. It is empty and harmless. Leave it or delete it later from the console.

## Private Link, manual

Delete only what you created in [Private Link manual setup](../setup-private-link-manual.md). Never delete a hub zone that other teams use.

```bash
az network private-endpoint delete --name "$PE_NAME" --resource-group "$RG"

az network private-dns link vnet delete --resource-group "$RG" \
  --zone-name "$ZONE" --name "${PE_NAME}-vnet-link" --yes
az network private-dns zone delete --resource-group "$RG" --name "$ZONE" --yes
```

Deleting the zone removes the A records inside it. If you created an `<orch>.neo4j.io` zone, delete its link and the zone the same way.

If you used `scripts/private_link.py`, run it with `--dry-run` first to list what it would change, then use the commands above.

## Private Link, Terraform

```bash
terraform -chdir=infra/terraform/azure-private-endpoint destroy
```

This removes the private endpoint and, when `manage_private_dns = true`, the private DNS zone, the VNet link, and the A record. With `manage_private_dns = false` the stack did not create a zone, so a central hub zone is left alone. Remove any routing-host A records you added by hand in a hub zone yourself.

## Aura-side cleanup (manual, no API)

1. Open the Aura private endpoints page, as described in [Aura console Step 4](../shared/aura-console-steps.md#step-4-approve-the-private-endpoint-in-the-aura-console). Remove the now-orphaned private endpoint approval.
2. Optionally remove the consumer subscription from **Target Azure Subscription IDs** if nothing else uses it. For the NCC path this is the Databricks-managed subscription.

For the NCC path, the `neo4j` secret scope and the imported validation notebook remain in the workspace. Delete them by hand for a full reset.
