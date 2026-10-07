# Private Link end-to-end test

Run each block from the repository root, in one terminal, in order. Paste the output of each block back so the next step can be checked. Steps marked **Aura console** are manual.

Stop and report if a block fails. Later steps depend on earlier ones.

## 0. Check the starting point

```bash
cd /Users/ryanknight/projects/neo4j-field/azure-databricks-aura-privatelink
az account show --query "{name:name, id:id}" -o table
az network vnet subnet list -g rg-aura-pl-test --vnet-name vnet-consumer --query "[].{name:name, prefix:addressPrefix}" -o table
az databricks workspace list -g rg-aura-pl-test -o table
```

Expected: subscription `Business Development`, subnets `snet-pe` and `snet-vm`, and no workspace yet.

## 1. Destroy the old private endpoint

`destroy` needs `RG` in `.env`. The first line adds a newline when `.env` does not end with one, so the appended setting does not join the previous line.

```bash
[ -n "$(tail -c1 .env)" ] && echo >> .env
grep -q '^RG=' .env || echo 'RG="rg-aura-pl-test"' >> .env
grep -E '^(RG|VNET|VNET_RG)=' .env
```

```bash
uv run scripts/private_link.py destroy --dry-run
```

```bash
uv run scripts/private_link.py destroy
```

**Aura console:** the old endpoint connection is now orphaned. Remove it from the private endpoints page of the instance if it still shows.

## 2. Create the VNet-injected workspace

Add these two lines to `.env` first. The script and `private_link.py` both read them.

```bash
[ -n "$(tail -c1 .env)" ] && echo >> .env
grep -q '^WORKSPACE_NAME=' .env || echo 'WORKSPACE_NAME="dbx-aura-pl-test"' >> .env
grep -q '^PE_SUBNET=' .env || echo 'PE_SUBNET="snet-pe"' >> .env
grep -E '^(VNET|VNET_RG|RG|WORKSPACE_NAME|PE_SUBNET)=' .env
```

Preview, then create. Creation takes several minutes.

```bash
uv run scripts/databricks_vnet_workspace.py up --dry-run
```

```bash
uv run scripts/databricks_vnet_workspace.py up
```

Check the result:

```bash
az databricks workspace show -g rg-aura-pl-test -n dbx-aura-pl-test --query "{state:provisioningState, url:workspaceUrl, vnet:parameters.customVirtualNetworkId.value}" -o json
```

Expected: `state` is `Succeeded` and `vnet` ends in `vnet-consumer`.

## 3. Create a Databricks CLI profile for the new workspace

A browser window opens for the login.

```bash
export WS_URL="https://$(az databricks workspace show -g rg-aura-pl-test -n dbx-aura-pl-test --query workspaceUrl -o tsv)"
echo "$WS_URL"
databricks auth login --host "$WS_URL" --profile azure-vnet-injected
databricks current-user me --profile azure-vnet-injected
```

Point `.env` at the new profile. `create-secret-scope.sh` reads `WORKSPACE_PROFILE`.

```bash
sed -i '' 's/^WORKSPACE_PROFILE=.*/WORKSPACE_PROFILE="azure-vnet-injected"/' .env
grep '^WORKSPACE_PROFILE=' .env
```

## 4. Set up Private Link

**Aura console, before this step:** add subscription `47fd4ce5-a912-480e-bb81-95fbd59bb6c5` to Target Azure Subscription IDs, as in `docs/shared/aura-console-steps.md`.

Preview:

```bash
uv run scripts/private_link.py run --dry-run
```

Run it. It pauses with exit code 2 when it needs the Aura approval.

```bash
uv run scripts/private_link.py run; echo "exit code: $?"
```

**Aura console:** approve the new pending endpoint connection. Then run the same command again until the exit code is 0.

```bash
uv run scripts/private_link.py run; echo "exit code: $?"
```

Check the state:

```bash
uv run scripts/private_link.py status
uv run scripts/private_link.py dns
```

## 5. Store the Neo4j secrets in the new workspace

```bash
./scripts/create-secret-scope.sh
databricks secrets list-secrets neo4j --profile azure-vnet-injected
```

Expected: keys `uri`, `username`, `password`, `database`.

## 6. Create a classic cluster

This starts billable compute. It terminates itself after 30 minutes idle.

```bash
databricks clusters spark-versions --profile azure-vnet-injected -o json | jq -r '.versions[] | select(.name | test("LTS")) | select(.key | test("ml|gpu|photon|aarch64") | not) | .key' | sort -V | tail -3
databricks clusters list-node-types --profile azure-vnet-injected -o json | jq -r '.node_types[] | select(.node_type_id=="Standard_DS3_v2") | .node_type_id'
```

Use the newest key from the first command as `SPARK_VERSION`. The second command should print `Standard_DS3_v2`. If it prints nothing, pick another node type from `list-node-types`.

```bash
export SPARK_VERSION="<newest key from above>"
databricks clusters create --profile azure-vnet-injected --no-wait --json "{
  \"cluster_name\": \"pl-test\",
  \"spark_version\": \"$SPARK_VERSION\",
  \"node_type_id\": \"Standard_DS3_v2\",
  \"num_workers\": 0,
  \"autotermination_minutes\": 30,
  \"data_security_mode\": \"SINGLE_USER\",
  \"spark_conf\": {\"spark.databricks.cluster.profile\": \"singleNode\", \"spark.master\": \"local[*]\"},
  \"custom_tags\": {\"ResourceClass\": \"SingleNode\"}
}"
```

Wait until the cluster is `RUNNING`. This takes 5 to 10 minutes.

```bash
export CLUSTER_ID=$(databricks clusters list --profile azure-vnet-injected -o json | jq -r '.[] | select(.cluster_name=="pl-test") | .cluster_id')
echo "$CLUSTER_ID"
databricks clusters get "$CLUSTER_ID" --profile azure-vnet-injected -o json | jq -r '.state, .state_message'
```

Repeat the last line until it prints `RUNNING`.

## 7. Upload the Private Link notebooks

```bash
databricks workspace import-dir pl-notebooks /Shared/aura-privatelink-pl --overwrite --profile azure-vnet-injected
databricks workspace list /Shared/aura-privatelink-pl --profile azure-vnet-injected
```

## 8. Find the private endpoint IP

The notebooks check that the Aura host resolves to this exact address. It is the IP of the private endpoint network interface.

```bash
export PE_NAME=$(az network private-endpoint list -g rg-aura-pl-test --query "[0].name" -o tsv)
export PE_IP=$(az network nic show --ids "$(az network private-endpoint show -g rg-aura-pl-test -n "$PE_NAME" --query 'networkInterfaces[0].id' -o tsv)" --query 'ipConfigurations[0].privateIPAddress' -o tsv)
echo "$PE_NAME $PE_IP"
```

Expected: `pe-b7253d3b-uksouth 10.10.1.4`. `uv run scripts/private_link.py verify` also prints it as `endpoint IP`.

## 9. Run the validation in the workspace

The notebooks are already in the workspace at `/Shared/aura-privatelink-pl/`. Run them there, not from the CLI.

1. Open the workspace URL.

   ```bash
   echo "$WS_URL"
   ```

2. Open **Workspace > Shared > aura-privatelink-pl > 01_validate_connectivity**.
3. In the compute dropdown at the top, attach the cluster `pl-test`. It must show `RUNNING`.
4. Fill in the widgets at the top of the notebook. Use the IP from step 8.
   - `expected_pe_ip`: the value of `$PE_IP`, for example `10.10.1.4`
   - `use_resolver`: `false`
5. Click **Run all**.

Passing means every cell finishes and the last cell prints that the check passed. Before the routing-host records exist, the routing-host cell fails and lists each host. That is the expected first result, so go to step 10.

Databricks masks the word `neo4j` in cell output as `[REDACTED]`, because the `username` and `database` secrets are both `neo4j`. A host such as `p-b7253d3b-a464-0009.production-orch-0477.[REDACTED].io` really ends in `.neo4j.io`.

## 10. Add routing-host records if step 9 failed on them

Run one command per failing host. These are the three hosts from the first run. Check the host names against the notebook output, replacing `[REDACTED]` with `neo4j`.

```bash
uv run scripts/private_link.py add-routing-host "p-b7253d3b-a464-0009.production-orch-0477.neo4j.io"
uv run scripts/private_link.py add-routing-host "p-b7253d3b-a464-0010.production-orch-0477.neo4j.io"
uv run scripts/private_link.py add-routing-host "p-b7253d3b-a464-0011.production-orch-0477.neo4j.io"
```

Restart the cluster so it drops cached DNS answers. Wait for `RUNNING`.

```bash
databricks clusters restart "$CLUSTER_ID" --profile azure-vnet-injected
databricks clusters get "$CLUSTER_ID" --profile azure-vnet-injected -o json | jq -r '.state'
```

Then run `01_validate_connectivity` again, with the same widget values. It should pass.

Run the other notebooks in this order, attached to `pl-test`:

1. `04_smoke_test`: same two widgets as `01`. It needs no tables.
2. `03_push_pull_demo`: set `use_resolver` to `false`. It needs no tables.
3. `02_delta_to_neo4j`: it expects a Unity Catalog catalog named `pldemo`. Create that catalog, or change `CATALOG` near the top of the notebook to `dbx_aura_pl_test`.

## 11. Tear down when finished

Delete the cluster, then the workspace and its subnets, then the private endpoint.

```bash
databricks clusters permanent-delete "$CLUSTER_ID" --profile azure-vnet-injected
uv run scripts/databricks_vnet_workspace.py down --dry-run
```

```bash
uv run scripts/databricks_vnet_workspace.py down
uv run scripts/private_link.py destroy
```
