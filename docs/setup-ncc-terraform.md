# NCC Terraform setup

This guide takes a clean Databricks Serverless workspace to a passing Neo4j Aura Private Link validation run with Terraform and `scripts/automate.py`. To do the Databricks steps by hand instead, see [NCC manual setup](setup-ncc-manual.md). The Aura console steps are in [Aura console steps](shared/aura-console-steps.md).

The orchestrator is re-entrant: run it, do the Aura console action it asks for, and run it again. A single command drives Terraform, polls the NCC rule to `ESTABLISHED`, restarts running SQL warehouses, populates the `neo4j` secret scope, and runs a validation notebook on serverless. Two steps pause on a human working in the Aura console.

The orchestrator exits `0` on success and `1` on an error or a failed validation. It exits `2` when it pauses. Do the action it names, then run it again.

## Start here: pick your path

- **Clean or new workspace:** The workspace has no prior NCC wiring. Follow this document top to bottom, starting at [Prerequisites](#prerequisites).
- **Half-configured workspace:** The workspace has stale NCCs, an expired rule, or a wrong-region binding left from an earlier attempt. Tear down the existing wiring **first**, as in [Teardown](operations/teardown.md), then return here and run the orchestrator. The flow below does not converge against a broken binding.

## Prerequisites

The workspace, account, Azure, and Aura requirements are in the [NCC manual setup prerequisites](setup-ncc-manual.md#prerequisites). The profile names, account ID, and Neo4j credentials below live in the repo-root `.env`. [Environment setup](env-setup.md) says where to find each one and how to load them with `source scripts/load-env.sh`.

The Terraform path adds these:

| Item | Value |
|------|-------|
| Terraform | 1.6.0 or later |
| Databricks Terraform provider | 1.55.0 or later. This version adds `databricks_mws_ncc_binding`. `terraform init` installs it. |
| Tools | `uv`, `terraform`, `az`, and the `databricks` CLI on `PATH` |

The stack authenticates through the Azure CLI by default. For CI/CD, it can use a service principal with the Databricks account admin role instead. Fill in the three `azure_*` variables described in [`terraform.tfvars.example`](../infra/terraform/databricks-ncc/terraform.tfvars.example).

Complete these one-time steps before the first run:

1. **Azure login.** Sign in as a Databricks account admin against the correct tenant and subscription:

   ```bash
   az login --tenant <TENANT_ID>
   az account set --subscription <SUB_ID>
   ```

   - **Tenant ID:** In the Azure portal, open **Microsoft Entra ID** and read **Tenant ID** under **Basic information** on the **Overview** page. From the CLI, run `az account show --query tenantId -o tsv`. Use the tenant that owns the subscription your Databricks workspace is deployed in.
   - **Subscription ID:** This is a separate value, listed under **Subscriptions** in the portal.

2. **Workspace CLI profile.** The profile must exist and authenticate. Create the repo-root `.env` from the sample file if it does not exist yet, and set `WORKSPACE_PROFILE` and `WORKSPACE_URL` in it. Then load it into your shell. The commands in this guide and the notebook upload in the manual guide both read `WORKSPACE_PROFILE`. Run the load command again after each edit to `.env`:

   ```bash
   [ -f .env ] || cp env.sample .env
   source scripts/load-env.sh
   databricks --profile "$WORKSPACE_PROFILE" current-user me
   ```

   If it reports stored credentials from an older CLI version, sign in again:

   ```bash
   databricks auth login --host "$WORKSPACE_URL" --profile "$WORKSPACE_PROFILE"
   ```

3. **Account-console CLI profile.** NCC rule polling targets `accounts.azuredatabricks.net`, which is a different auth context from the workspace. Set `ACCOUNT_PROFILE` and `DATABRICKS_ACCOUNT_ID` in `.env`, load it again, then sign in once:

   ```bash
   source scripts/load-env.sh
   databricks auth login --host https://accounts.azuredatabricks.net \
     --account-id "$DATABRICKS_ACCOUNT_ID" --profile "$ACCOUNT_PROFILE"
   ```

   Verify that it lists NCCs:

   ```bash
   databricks --profile "$ACCOUNT_PROFILE" account network-connectivity list-network-connectivity-configurations
   ```

   Any JSON list counts as a pass. Check that each entry shows the `account_id` from the login command above. An auth or permission error means the profile cannot reach the account console. The NCCs listed are existing ones in the account, including expired rules and rules that belong to other people. Leave them alone, because Terraform creates a new NCC for this run.

4. **Terraform variables.** Copy the example to `infra/terraform/databricks-ncc/terraform.tfvars`. Fill in the account ID, workspace ID and URL, region, Aura PLS alias, and Aura hostname. The Aura PLS alias and hostname come from the Aura console, so complete [Aura console Steps 1 and 2](shared/aura-console-steps.md#step-1-provision-neo4j-aura-vdc-on-azure) first:

   ```bash
   # if not already present, then edit:
   cp infra/terraform/databricks-ncc/terraform.tfvars.example \
      infra/terraform/databricks-ncc/terraform.tfvars
   ```

5. **Neo4j values in `.env`.** `automate.py` loads `.env` at startup, so it needs no `source` step for these values. Add the Neo4j credentials to the `.env` you created in step 2:

   ```bash
   # .env (gitignored)
   WORKSPACE_PROFILE="<workspace-profile>"
   NEO4J_URI="neo4j+s://<aura-instance-id>.databases.neo4j.io"
   NEO4J_USERNAME="neo4j"
   NEO4J_PASSWORD="<aura-password>"
   NEO4J_DATABASE="neo4j"   # optional, defaults to neo4j
   ```

   `WORKSPACE_PROFILE` is the default for `--workspace-profile`. The `NEO4J_*` values fill the `neo4j` secret scope. The URI host must match `aura_private_hostname` in `terraform.tfvars`. An exported environment variable overrides the `.env` value.

## Pre-flight check: the `neo4j` secret scope

The scope name is fixed as `neo4j`. When the scope already holds all four keys, the secrets step skips the write and leaves the values unchanged. A scope that holds another Aura instance's credentials therefore stays in place, and the validation run connects with those credentials. Inspect the scope first:

```bash
databricks --profile "$WORKSPACE_PROFILE" secrets list-secrets neo4j
```

- If the scope is absent, or already holds `uri`, `username`, `password`, and `database` for the target instance, proceed.
- If the scope is missing any of the four keys, proceed. The secrets step writes all four from `.env`.
- If the scope holds another instance's credentials, pass `--reset-secret-scope` on the run to replace it. Confirm first that nothing else in the workspace uses that scope.

`--reset-secret-scope` deletes the scope and recreates it from the `NEO4J_*` values in `.env` in one step. It checks those values before the delete, so it never removes a scope it cannot repopulate:

```bash
uv run scripts/automate.py run --account-profile "$ACCOUNT_PROFILE" --reset-secret-scope
```

To delete the scope by hand instead, run this command, then run the orchestrator as usual:

```bash
databricks --profile "$WORKSPACE_PROFILE" secrets delete-scope neo4j
```

## Run the orchestrator

### 1. Run it

From the repo root:

```bash
uv run scripts/automate.py run --account-profile "$ACCOUNT_PROFILE"
```

`--account-profile` is required. `--workspace-profile` defaults to `WORKSPACE_PROFILE` from `.env`. The run stops with an error if neither is set. Pass `--workspace-profile <name>` to target a different workspace profile.

The first Terraform apply fails on the subscription allow-list. This failure is expected, and it produces pause 1 below.

### 2. Pause 1: Aura subscription allow-list

The first apply fails with `ThirdPartyPrivateLinkServiceProvidedDuringPrivateEndpointCreationDoesNotExistOrIsNotVisible`. The private endpoint request comes from a Databricks-managed subscription that Aura does not trust yet. The orchestrator prints the managed subscription GUID and exits with code `2`.

Do this, then re-run:

1. Open the Aura private endpoints page, as described in [Aura console Step 3](shared/aura-console-steps.md#step-3-allow-list-the-consumer-subscription).
2. Add the printed subscription GUID to **Target Azure Subscription IDs**.
3. Re-run the same command:

   ```bash
   uv run scripts/automate.py run --account-profile "$ACCOUNT_PROFILE"
   ```

Databricks can retry from more than one managed subscription per region, so this pause can repeat. Add each GUID the tool reports, then re-run.

### 3. Pause 2: approve the private endpoint

Once the apply succeeds, the orchestrator polls the NCC rule and reports `PENDING`. It prints the approval instruction when it first sees `PENDING`. If the rule does not reach `ESTABLISHED` within the poll timeout, it exits `2`. The default timeout is 600 seconds.

Do this, then re-run:

1. Approve the private endpoint in the Aura console, as described in [Aura console Step 4](shared/aura-console-steps.md#step-4-approve-the-private-endpoint-in-the-aura-console). Approve it promptly.
2. Re-run the same command. The poller observes `ESTABLISHED` and continues.

An NCC private endpoint rule left `PENDING` for 14 days expires. The poller warns once a `PENDING` rule is 12 days old. If a rule reaches `REJECTED`, `DISCONNECTED`, `EXPIRED`, or `CREATE_FAILED`, the tool fails fast and tells you to recreate it:

```bash
terraform -chdir=infra/terraform/databricks-ncc taint databricks_mws_ncc_private_endpoint_rule.aura
terraform -chdir=infra/terraform/databricks-ncc apply
```

### 4. Steps that run without a pause

After `ESTABLISHED`, the same invocation continues:

- **Warehouses.** Running SQL warehouses are stopped and started so they pick up NCC-managed DNS. With none running, this step does nothing. Pass `--skip-warehouse-restart` to leave active SQL sessions untouched on re-runs.
- **Secrets.** If the `neo4j` scope is missing, or lacks any of `uri`, `username`, `password`, and `database`, the orchestrator creates the scope and sets all four keys from the `NEO4J_*` values in `.env`. If all four keys exist, it skips the write. It uses the workspace SDK client and the `--workspace-profile` auth that client already holds, so no bearer token is minted and no helper subprocess runs. `scripts/create-secret-scope.sh` remains for CLI-only environments.
- **Validation notebook.** The orchestrator imports `ncc-notebooks/01_validate_connectivity.py` to `/Shared/aura-privatelink/01_validate_connectivity` in the workspace, overwriting any earlier copy. It submits that notebook as a one-time serverless run. The run ID prints before the wait, so you can find the run in the Jobs UI. The default wait is 30 minutes. If the run does not finish in time, the tool pauses and exits `2`. Check the run in the Jobs UI, then re-run.

On success the tool prints `SUCCESS` and exits `0`.

### Expected pause count

From a clean workspace, expect **two kinds of human stop**, both in the Aura console: the subscription allow-list add and the private endpoint approval.

The allow-list pause can repeat once per GUID, because Databricks can retry from more than one managed subscription. Routing hosts add no stop, because the validation notebook resolves them itself. Any other stop is a failure worth investigating.

## Reference

### Flags

| Flag | Default | Purpose |
|---|---|---|
| `--account-profile` | Required | CLI profile for `accounts.azuredatabricks.net`. Every run polls the rule, including `--no-apply`. |
| `--workspace-profile` | `WORKSPACE_PROFILE` from `.env` | CLI profile for the target workspace. The run errors if neither the flag nor `WORKSPACE_PROFILE` is set. |
| `--no-apply` | off | Skip `terraform apply`. Read `terraform output -json` only. |
| `--notebook PATH` | `ncc-notebooks/01_validate_connectivity.py` | Validation notebook to run. |
| `--poll-timeout N` | `600` | Seconds to wait for `ESTABLISHED`. |
| `--poll-interval N` | `15` | Seconds between rule status polls. |
| `--run-timeout N` | `30` | Minutes to wait for the validation run. |
| `--skip-warehouse-restart` | off | Do not stop and start running SQL warehouses. |
| `--reset-secret-scope` | off | Delete the `neo4j` secret scope before recreating it. |

### Read-only re-run

After a successful run, you can re-check the outputs and re-run validation without applying infrastructure:

```bash
uv run scripts/automate.py run --no-apply --skip-warehouse-restart --account-profile "$ACCOUNT_PROFILE"
```

This reads `terraform output -json` instead of applying. It fails with a clear message if the stack has not been applied yet. `--no-apply` alone still restarts running SQL warehouses, so add `--skip-warehouse-restart` to leave them untouched.

### Routing hosts for clients without a resolver

Aura VDC returns Bolt routing hosts such as `p-<aura-instance-id>-<suffix>.<orch>.neo4j.io` in its routing table. All four repository notebooks install a Neo4j driver resolver that maps these hosts back to the instance host. The default run therefore passes with only `aura_private_hostname` in the rule.

A client without such a resolver fails with `Cannot resolve address p-...neo4j.io:7687`. Your own jobs and apps on serverless fall in this group. The error names the host. Add each host it names to `aura_extra_domain_names` in `infra/terraform/databricks-ncc/terraform.tfvars`:

```hcl
aura_extra_domain_names = ["p-<aura-instance-id>-<suffix>.<orch>.neo4j.io"]
```

Then re-run the orchestrator. The run applies the updated stack, and Terraform adds the hosts to the rule's `domain_names`. The CLI equivalent is in [Add a routing hostname later](setup-ncc-manual.md#add-a-routing-hostname-later).

The orchestrator reports routing hosts only when you pass `--notebook` with a notebook that has no resolver. In that case it scans the failed run's output for this error, prints the `aura_extra_domain_names` line to add, and exits `1`. It does not edit `terraform.tfvars`.

### Why the stack uses `databricks_mws_ncc_binding`

The stack attaches the NCC with `databricks_mws_ncc_binding` instead of `databricks_mws_workspaces`. `databricks_mws_workspaces` manages the full workspace lifecycle. Applied to a workspace created outside Terraform, it tries to reconcile every workspace attribute it knows about. That produces spurious diffs and risks unintended workspace changes. `databricks_mws_ncc_binding` manages only the association between the NCC and the workspace.

## Validate connectivity

On success the orchestrator has already created the `neo4j` secret scope. It has also run [ncc-notebooks/01_validate_connectivity.py](../ncc-notebooks/01_validate_connectivity.py) on serverless compute from `/Shared/aura-privatelink/01_validate_connectivity`. It imports only that one notebook.

To run all four notebooks yourself, including the smoke test in [ncc-notebooks/04_smoke_test.py](../ncc-notebooks/04_smoke_test.py), follow [Validate connectivity](setup-ncc-manual.md#validate-connectivity) in the NCC manual guide. Skip its secret scope step. Its upload snippet reads the `WORKSPACE_PROFILE` you exported in [Prerequisites](#prerequisites). It copies the notebooks to `/Users/<your-user-name>/neo4j-privatelink`, which is a different folder from the orchestrator's `/Shared/aura-privatelink/`.

## Close the public endpoint

The NCC rule adds a private path. It does not close the public one. After validation succeeds, disable public access in the Aura console, as in [Aura console Step 5](shared/aura-console-steps.md#step-5-disable-public-access-on-aura). Run the outside-in check from that step, then run the validation again with the [read-only re-run](#read-only-re-run).

Disable public access only after validation succeeds. Doing it earlier can lock you out while you debug.

## Teardown

Follow [Teardown: NCC](operations/teardown.md#ncc-databricks-serverless), then the [Aura-side cleanup](operations/teardown.md#aura-side-cleanup-manual-no-api). `terraform destroy` cannot delete the NCC itself, so teardown swaps in a placeholder NCC before it deletes the original.

## What's next

| Topic | Guide |
|-------|-------|
| Developer laptop needs Neo4j Desktop or a browser after public access is off | [Developer desktop access](operations/developer-desktop-access.md) |
| Batch jobs, pipelines, or services in other VNets or subscriptions | [Batch jobs in other VNets](operations/batch-jobs-other-vnets.md) |
| Something does not resolve or connect | [Troubleshooting](operations/troubleshooting.md) |
| Run it in production | [Production notes](operations/production-notes.md) |
