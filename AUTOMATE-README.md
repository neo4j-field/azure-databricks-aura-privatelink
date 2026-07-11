# Running `scripts/automate.py`

Exact operator steps to drive a clean Databricks Serverless workspace to a passing
Neo4j Aura Private Link validation run.

The orchestrator is re-entrant: run it, do the Aura-console action it asks for, run it
again. A single command drives Terraform, polls the NCC rule to `ESTABLISHED`, restarts
running SQL warehouses, populates the `neo4j` secret scope, and runs a validation notebook
on serverless. Two steps pause on a human working in the Aura console.

Exit codes: `0` success, `1` error or validation failed, `2` paused (do the named action,
then re-run).

---

## Start here — pick your path

- **Clean or new workspace** (no prior NCC wiring): follow this document top to bottom,
  starting at *Prerequisites*.
- **Rebuilding a half-configured workspace** (stale NCCs, an expired rule, a wrong-region
  binding left from an earlier attempt): tear down the existing wiring **first** (see
  [Teardown](README.md#teardown)), then return here and run the orchestrator. Running the
  flow below against half-configured state operates on a broken binding and will not
  converge.

---

## Prerequisites (one time)

1. **Tools.** `uv`, `terraform`, `az`, and the `databricks` CLI on `PATH`.

2. **Azure login** as a Databricks account admin against the correct tenant and subscription:

   ```bash
   az login --tenant <TENANT_ID>
   az account set --subscription <SUB_ID>
   ```

3. **Workspace CLI profile** (default `azure-rk-knight`) exists and authenticates:

   ```bash
   databricks --profile azure-rk-knight current-user me
   ```

4. **Account-console CLI profile.** NCC rule polling targets `accounts.azuredatabricks.net`,
   a different auth context from the workspace. Configure it once:

   ```bash
   databricks auth login --host https://accounts.azuredatabricks.net --account-id 16604dc9-1c39-4d73-95a3-7d75ce00a12b
   ```

   Verify it lists NCCs (this repo uses the `azure-neo4j-account` profile):

   ```bash
   databricks --profile azure-neo4j-account account network-connectivity list-network-connectivity-configurations
   ```

5. **Terraform variables.** Populate `infra/terraform/databricks-ncc/terraform.tfvars` from
   the example, confirming the account id, workspace id/url, region, Aura PLS alias, and Aura
   hostname:

   ```bash
   # if not already present, then edit:
   cp infra/terraform/databricks-ncc/terraform.tfvars.example \
      infra/terraform/databricks-ncc/terraform.tfvars
   ```

6. **Aura credentials** for the `neo4j` secret scope, in the repo-root `.env`.
   `automate.py` loads it automatically at startup (python-dotenv), so no
   `export` or `source` step is needed:

   ```bash
   # .env (gitignored)
   NEO4J_URI="neo4j+s://<dbid>.databases.neo4j.io"
   NEO4J_USERNAME="neo4j"
   NEO4J_PASSWORD="..."
   NEO4J_DATABASE="neo4j"          # optional, defaults to neo4j
   ```

   A real environment variable, if exported, still overrides the `.env` value.

---

## Pre-flight check: the `neo4j` secret scope

The scope name is hardcoded to `neo4j`. If a scope named `neo4j` already exists for a
different Aura instance, the secrets step will repopulate it and can break whatever else uses
it. Inspect it first:

```bash
databricks --profile azure-rk-knight secrets list-secrets neo4j
```

- If the scope is absent, or already holds `uri`, `username`, `password`, `database` for the
  target instance, proceed.
- If it holds another instance's credentials, resolve that before running (use a fresh
  workspace, remove the stale scope, or confirm the overwrite is intended). The secrets step
  treats the scope as incomplete whenever any of the four expected keys is missing and
  repopulates it.

To remove a stale scope, either delete it by hand:

```bash
databricks --profile azure-rk-knight secrets delete-scope neo4j
```

or pass `--reset-secret-scope` on the run, which deletes the scope and recreates it from the
`NEO4J_*` values in `.env` in one step (the values are checked before the delete, so it
never removes a scope it cannot repopulate):

```bash
uv run scripts/automate.py run --account-profile azure-neo4j-account --reset-secret-scope
```

---

## Run the orchestrator

### 1. Run it

From the repo root:

```bash
uv run scripts/automate.py run --account-profile azure-neo4j-account
```

The workspace profile defaults to `azure-rk-knight`; pass `--workspace-profile <name>` to
change it.

On the first run the Terraform apply is expected to fail with the subscription allow-list
gotcha. That is by design and produces pause #1 below.

### 2. Pause #1: Aura subscription allow-list

The first apply fails with
`ThirdPartyPrivateLinkServiceProvidedDuringPrivateEndpointCreationDoesNotExistOrIsNotVisible`
because the private endpoint originates from a Databricks-managed subscription that Aura does
not yet trust. The orchestrator prints the managed subscription GUID and exits with code `2`.

Do this, then re-run:

1. Open the Aura console, your instance, Network Access.
2. Add the printed subscription GUID to **Target Azure Subscription IDs**.
3. Re-run the same command:

   ```bash
   uv run scripts/automate.py run --account-profile azure-neo4j-account
   ```

Databricks may retry from more than one managed subscription per region, so this can iterate.
Add each GUID the tool surfaces, then re-run.

### 3. Pause #2: approve the private endpoint

Once apply succeeds, the orchestrator polls the NCC rule and reports `PENDING`, then prints
the approval instruction and exits `2` if it does not reach `ESTABLISHED` within the timeout
(default 600s).

Do this, then re-run:

1. In the Aura console: Security, Network Access, Pending approvals, Approve.
2. Re-run the same command. The poller observes `ESTABLISHED` and continues.

A rule left `PENDING` for 14 days expires; the poller warns as it approaches that limit. If a
rule reaches `REJECTED`, `DISCONNECTED`, `EXPIRED`, or `CREATE_FAILED`, the tool fails fast
and tells you to recreate it:

```bash
terraform -chdir=infra/terraform/databricks-ncc taint databricks_mws_ncc_private_endpoint_rule.aura
terraform -chdir=infra/terraform/databricks-ncc apply
```

### 4. Steps that run without a pause

After `ESTABLISHED`, the same invocation continues:

- **Warehouses.** Running SQL warehouses are stopped and started so they pick up NCC-managed
  DNS. With none running, this is a no-op. Pass `--skip-warehouse-restart` to leave active SQL
  sessions untouched on re-runs.
- **Secrets.** If the `neo4j` scope or any of `uri`/`username`/`password`/`database` is
  missing, the orchestrator creates the scope and sets the four keys directly through the
  workspace SDK client, using the `NEO4J_*` values loaded from `.env`. It reuses the
  `--workspace-profile` auth the client already holds, so no bearer token is minted and no
  helper subprocess runs. (`scripts/create-secret-scope.sh` remains for CLI-only environments.)
- **Validation notebook.** `notebooks/01_validate_connectivity.py` is imported to
  `/Shared/aura-privatelink/` (overwrite) and submitted as a one-time serverless run. The run
  id prints before the wait, so the run is findable in the Jobs UI. Default wait is 30 minutes.

On success the tool prints `SUCCESS` and exits `0`.

### 5. Routing-host fallback (reactive)

If the validation run fails because an Aura routing host does not resolve
(`Cannot resolve address p-...neo4j.io:7687`), the tool prints the exact line to add:

```
aura_extra_domain_names = ["p-....neo4j.io"]
```

The orchestrator is print-only here and does not edit `terraform.tfvars`. Add the line
yourself in `infra/terraform/databricks-ncc/terraform.tfvars`, then:

```bash
terraform -chdir=infra/terraform/databricks-ncc apply
uv run scripts/automate.py run --account-profile azure-neo4j-account
```

### Expected pause count

From a clean workspace, reaching a passing validation run should take **exactly two human
pauses**, both in the Aura console: the subscription allow-list add and the private endpoint
approval. More than two is a failure worth investigating.

---

## Reference

### Flags

| Flag | Default | Purpose |
|---|---|---|
| `--account-profile` | (required for polling) | CLI profile for `accounts.azuredatabricks.net`. |
| `--workspace-profile` | `azure-rk-knight` | CLI profile for the target workspace. |
| `--no-apply` | off | Skip `terraform apply`; only read `terraform output -json`. |
| `--notebook PATH` | `notebooks/01_validate_connectivity.py` | Validation notebook to run. |
| `--poll-timeout N` | `600` | Seconds to wait for `ESTABLISHED`. |
| `--poll-interval N` | `15` | Seconds between rule status polls. |
| `--run-timeout N` | `30` | Minutes to wait for the validation run. |
| `--skip-warehouse-restart` | off | Do not stop/start running SQL warehouses. |
| `--reset-secret-scope` | off | Delete the `neo4j` secret scope before recreating it. |

### Read-only re-run

To re-check outputs and re-run validation without applying infrastructure (for example after
success), skip the apply:

```bash
uv run scripts/automate.py run --no-apply --account-profile azure-neo4j-account
```

This reads `terraform output -json` instead of applying. It fails with a clear message if the
stack has not been applied yet.
