# Teardown

Pick the section for the path you used, then finish with the [Aura-side cleanup](#aura-side-cleanup-manual-no-api). The Aura console has no API, so that cleanup is always manual.

The commands reuse the variables from setup. Load them with `source scripts/load-env.sh`, as in [Environment setup](../env-setup.md).

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

If you built the setup by hand, run only the manual part of Step 1, then continue at Step 2.

### Step 1: Destroy the stack

With Terraform, save the NCC id first. Step 3 deletes the NCC by this id:

```bash
export ORIGINAL_NCC_ID="$(terraform -chdir=infra/terraform/databricks-ncc output -raw ncc_id)"
terraform -chdir=infra/terraform/databricks-ncc destroy
```

This removes the private endpoint rule and the workspace binding, then fails on the NCC with the message above.

With the manual setup, delete the rule with the CLI command in [Recreate an expired or failed rule](../setup-ncc-manual.md#recreate-an-expired-or-failed-rule). Then save the `NCC_ID` that the manual guide exported in its Step 1:

```bash
export ORIGINAL_NCC_ID="$NCC_ID"
```

### Step 2: Detach the NCC by swapping in a placeholder

The workspace must always point at *some* NCC, so free the original by pointing the workspace at an empty placeholder instead. The placeholder's region **must match the workspace region**. An NCC only binds to a workspace in its own region.

The commands reuse `ACCOUNT_PROFILE`, `WORKSPACE_ID`, and `NCC_REGION` from [NCC manual setup Step 0](../setup-ncc-manual.md#step-0-sign-in-and-collect-your-values). In a fresh shell, run `LOAD_ENV_LOOKUP=1 source scripts/load-env.sh` to set `WORKSPACE_ID` again. Create the placeholder NCC, then swap the workspace onto it:

```bash
export PLACEHOLDER_NCC_ID="$(databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  create-network-connectivity-configuration ncc-placeholder "$NCC_REGION" -o json \
  | jq -r .network_connectivity_config_id)"

databricks --profile "$ACCOUNT_PROFILE" account workspaces update "$WORKSPACE_ID" \
  --network-connectivity-config-id "$PLACEHOLDER_NCC_ID"
```

The update waits for the workspace to return to `RUNNING`. The swap frees the original NCC.

**Console alternative:** Open **Workspaces** in the account console, select your workspace, and click **Update workspace**. Under **Network connectivity configurations**, pick a different NCC and click **Update**. If you have no other NCC, create an empty one first.

**REST alternative:** These calls mirror the [REST alternative in the NCC manual setup](../setup-ncc-manual.md#rest-alternative). They reuse `DATABRICKS_ACCOUNT_ID`, `WORKSPACE_ID`, and `NCC_REGION`. The account token comes from the same `az login`:

```bash
export DATABRICKS_TOKEN="$(az account get-access-token \
  --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d \
  --query accessToken -o tsv)"
BASE="https://accounts.azuredatabricks.net/api/2.0/accounts/${DATABRICKS_ACCOUNT_ID}"

# a. Create an empty placeholder NCC in the workspace's region.
export PLACEHOLDER_NCC_ID="$(curl -s -X POST "${BASE}/network-connectivity-configs" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}" -H "Content-Type: application/json" \
  --data "{\"name\": \"ncc-placeholder\", \"region\": \"${NCC_REGION}\"}" \
  | jq -r .network_connectivity_config_id)"

# b. Swap the workspace onto the placeholder. This frees the original NCC.
curl -s -X PATCH "${BASE}/workspaces/${WORKSPACE_ID}" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}" -H "Content-Type: application/json" \
  --data "{\"network_connectivity_config_id\": \"${PLACEHOLDER_NCC_ID}\"}"
```

### Step 3: Delete the original NCC and reconcile Terraform

Delete the original NCC by the id you saved in Step 1:

```bash
databricks --profile "$ACCOUNT_PROFILE" account network-connectivity \
  delete-network-connectivity-configuration "$ORIGINAL_NCC_ID"
```

With REST, reuse `BASE` and `DATABRICKS_TOKEN` from Step 2:

```bash
curl -s -X DELETE "${BASE}/network-connectivity-configs/${ORIGINAL_NCC_ID}" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}"
```

With Terraform, drop the deleted NCC from state:

```bash
terraform -chdir=infra/terraform/databricks-ncc state rm \
  databricks_mws_network_connectivity_config.ncc
```

The `state rm` drops the already-deleted NCC from Terraform state so the stack reads clean. Skip it if you built the setup by hand. The placeholder NCC stays attached to the workspace. It is empty and harmless. Leave it or delete it later from the console.

## Private Link, manual

Delete only what you created in [Private Link manual setup](../setup-private-link-manual.md). Never delete a hub zone that other teams use.

```bash
az network private-endpoint delete --name "$PE_NAME" --resource-group "$PE_RG"

az network private-dns link vnet delete --resource-group "$PE_RG" \
  --zone-name "$ZONE" --name "${PE_NAME}-vnet-link" --yes
az network private-dns zone delete --resource-group "$PE_RG" --name "$ZONE" --yes
```

Deleting the zone removes the A records inside it. If you created an `<orch>.neo4j.io` zone, delete its link and the zone the same way.

If you used `scripts/private_link.py`, use its `destroy` command instead of the commands above. Run it with `--dry-run` first to list what it would delete:

```bash
uv run scripts/private_link.py destroy --dry-run
uv run scripts/private_link.py destroy
```

## Private Link, Terraform

```bash
terraform -chdir=infra/terraform/azure-private-endpoint destroy
```

This removes the private endpoint and, when `manage_private_dns = true`, the private DNS zone, the VNet link, and the A record. With `manage_private_dns = false` the stack did not create a zone, so a central hub zone is left alone. Remove any routing-host A records you added by hand in a hub zone yourself.

## Aura-side cleanup (manual, no API)

1. Open the Aura private endpoints page, as described in [Aura console Step 4](../shared/aura-console-steps.md#step-4-approve-the-private-endpoint-in-the-aura-console). Remove the now-orphaned private endpoint approval.
2. Optionally remove the consumer subscription from **Target Azure Subscription IDs** if nothing else uses it. For the NCC path this is the Databricks-managed subscription.

For the NCC path, the `neo4j` secret scope and the imported notebooks remain in the workspace. `scripts/automate.py` imports the validation notebook into `/Shared/aura-privatelink`. The manual upload puts the notebooks in `/Users/<me>/neo4j-privatelink`. Delete the scope and the notebook folder by hand for a full reset.
