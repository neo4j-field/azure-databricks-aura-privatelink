#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "databricks-sdk>=0.30",
#     "python-dotenv>=1.0",
# ]
# ///
"""
automate.py - orchestrate the Databricks Serverless + Neo4j Aura Private Link setup.

This is the Phase 1 orchestrator described in automate-v2.md. It drives the
`databricks-ncc` Terraform stack and fills the runtime gaps that Terraform and
the standalone helper scripts do not cover: polling the NCC private endpoint
rule to ESTABLISHED, restarting running SQL warehouses so they pick up
NCC-managed DNS, populating the `neo4j` secret scope, and running a validation
notebook on serverless.

It is re-entrant. Two steps pause on a human working in the Aura console (adding
the Databricks-managed subscription to the allow-list, and approving the private
endpoint request), so every invocation inspects observable state and continues
from wherever things stand. The intended experience is: run it, it tells you
what to do in the Aura console, run it again.

Auth uses the Databricks SDK with CLI-profile OAuth:
  - --account-profile targets https://accounts.azuredatabricks.net (NCC rule status)
  - --workspace-profile targets the workspace URL (secrets, jobs, warehouses)

Configure the account profile once (Phase 1 prerequisite):
  databricks auth login --host https://accounts.azuredatabricks.net --account-id <account-id>

Aura credentials come from the repo-root .env (loaded at startup via python-dotenv):
  NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, and optional NEO4J_DATABASE (default neo4j).
Real environment variables, if set, take precedence over the .env file.

Usage:
  cd <repo-root>
  uv run scripts/automate.py run --account-profile <name> [--workspace-profile azure-rk-knight]
  uv run scripts/automate.py teardown [--yes]

`run` flags:
  --no-apply                Skip `terraform apply`; only read `terraform output -json`.
  --notebook PATH           Validation notebook (default: notebooks/01_validate_connectivity.py).
  --poll-timeout N          Seconds to wait for the rule to reach ESTABLISHED (default: 600).
  --poll-interval N         Seconds between rule status polls (default: 15).
  --run-timeout N           Minutes to wait for the validation run to finish (default: 30).
  --skip-warehouse-restart  Do not stop/start running SQL warehouses.
  --reset-secret-scope      Delete the `neo4j` secret scope before recreating it.

`teardown` flags:
  --yes                     Skip the confirmation prompt (for headless / CI use).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
TERRAFORM_DIR = REPO_ROOT / "infra" / "terraform" / "databricks-ncc"
DEFAULT_NOTEBOOK = REPO_ROOT / "notebooks" / "01_validate_connectivity.py"
WORKSPACE_NOTEBOOK_DIR = "/Shared/aura-privatelink"

SECRET_SCOPE = "neo4j"
SECRET_KEYS = ("uri", "username", "password", "database")

# The undocumented gotcha: the first apply fails because the Databricks-managed
# subscription that originates the private endpoint is not yet on Aura's
# "Target Azure Subscription IDs" allow-list. The failing subscription GUID
# appears in the error path, e.g. /subscriptions/<guid>/resourceGroups/prod-...
THIRD_PARTY_ERROR = (
    "ThirdPartyPrivateLinkServiceProvidedDuringPrivateEndpointCreation"
    "DoesNotExistOrIsNotVisible"
)
SUBSCRIPTION_PATH_RE = re.compile(
    r"/subscriptions/([0-9a-fA-F-]{36})/resourceGroups/(prod-[\w-]+)"
)

# Neo4j Aura VDC advertises Bolt routing hosts like p-<dbid>-....neo4j.io after
# the first connection. If those do not resolve through NCC DNS the driver
# raises "Cannot resolve address p-...neo4j.io:7687".
ROUTING_HOST_RE = re.compile(
    r"Cannot resolve address\s+(p-[a-z0-9][a-z0-9.\-]*\.neo4j\.io)", re.IGNORECASE
)

# NCC private endpoint rule connection states.
STATE_ESTABLISHED = "ESTABLISHED"
STATE_PENDING = "PENDING"
STATE_TERMINAL_BAD = {"REJECTED", "DISCONNECTED", "EXPIRED", "CREATE_FAILED"}


class PauseNeeded(Exception):
    """Raised when the flow must stop and wait on a human action, then a re-run."""


class SetupError(Exception):
    """Raised on an unrecoverable error that a re-run will not fix on its own."""


# ---------------------------------------------------------------------------
# Small console helpers
# ---------------------------------------------------------------------------

def banner(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def info(msg: str) -> None:
    print(f"  {msg}")


# ---------------------------------------------------------------------------
# Terraform
# ---------------------------------------------------------------------------

@dataclass
class TerraformOutputs:
    ncc_id: str
    rule_id: str


def _terraform(args: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    cmd = ["terraform", f"-chdir={TERRAFORM_DIR}", *args]
    info(f"$ {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        text=True,
        capture_output=capture,
        check=False,
    )


def read_terraform_outputs() -> TerraformOutputs:
    result = _terraform(["output", "-json"], capture=True)
    if result.returncode != 0:
        raise SetupError(
            "Could not read Terraform outputs. Has the stack been applied?\n"
            f"  {result.stderr.strip()}"
        )
    data = json.loads(result.stdout or "{}")
    try:
        ncc_id = data["ncc_id"]["value"]
        rule_id = data["private_endpoint_rule_id"]["value"]
    except KeyError as exc:
        raise SetupError(
            f"Terraform outputs missing expected key: {exc}. "
            "Re-run without --no-apply, or apply the stack first."
        ) from exc
    return TerraformOutputs(ncc_id=ncc_id, rule_id=rule_id)


def apply_stack() -> TerraformOutputs:
    """Run `terraform apply`, translating the ThirdParty allow-list failure into
    an actionable pause. Returns the stack outputs once apply succeeds."""
    banner("Step 1-2: Terraform apply (databricks-ncc)")

    init = _terraform(["init", "-input=false", "-no-color"], capture=True)
    if init.returncode != 0:
        raise SetupError(f"terraform init failed:\n{init.stdout}\n{init.stderr}")

    apply = _terraform(
        ["apply", "-input=false", "-no-color", "-auto-approve"], capture=True
    )
    combined = f"{apply.stdout}\n{apply.stderr}"

    if apply.returncode != 0:
        if THIRD_PARTY_ERROR in combined:
            _print_allowlist_instruction(combined)
            raise PauseNeeded(
                "Add the subscription above to Aura's allow-list, then re-run."
            )
        raise SetupError(f"terraform apply failed:\n{combined}")

    print(apply.stdout)
    return read_terraform_outputs()


def _print_allowlist_instruction(error_text: str) -> None:
    match = SUBSCRIPTION_PATH_RE.search(error_text)
    banner("ACTION REQUIRED - Aura subscription allow-list")
    print(
        "The private endpoint request originates from a Databricks-managed Azure\n"
        "subscription that Aura does not yet trust. This is expected on the first\n"
        "apply and is not documented in any public Neo4j or Microsoft source.\n"
    )
    if match:
        sub_id, rg = match.groups()
        print(f"  Managed subscription ID : {sub_id}")
        print(f"  (seen in resource group : {rg})\n")
    else:
        print(
            "  Could not parse the subscription ID automatically. Look for a\n"
            "  /subscriptions/<guid>/resourceGroups/prod-... path in the error above.\n"
        )
    print(
        "To fix:\n"
        "  1. Open the Aura console -> your instance -> Network Access.\n"
        "  2. Add the subscription ID above to 'Target Azure Subscription IDs'.\n"
        "  3. Re-run this command. Databricks may retry from more than one managed\n"
        "     subscription per region, so this can iterate - add each ID it surfaces.\n"
    )


# ---------------------------------------------------------------------------
# NCC rule polling (account profile)
# ---------------------------------------------------------------------------

def _rule_state(rule) -> str:
    """Extract the connection state from an NCC private endpoint rule, tolerant
    of SDK schema variations."""
    data = rule.as_dict()
    state = data.get("connection_state")
    if not state:
        # Newer schemas nest Azure PE details under a sub-object.
        state = data.get("azure_private_endpoint_rule", {}).get("connection_state")
    return (state or "").upper()


def _rule_age_days(rule) -> float | None:
    data = rule.as_dict()
    updated = data.get("updated_time") or data.get("azure_private_endpoint_rule", {}).get(
        "updated_time"
    )
    if not updated:
        return None
    # Databricks returns epoch milliseconds.
    now_ms = time.time() * 1000
    return max(0.0, (now_ms - float(updated)) / (1000 * 60 * 60 * 24))


def poll_rule(account_client, outputs: TerraformOutputs, timeout: int, interval: int) -> None:
    banner("Step 3: Poll NCC private endpoint rule -> ESTABLISHED")
    nc = account_client.network_connectivity
    deadline = time.monotonic() + timeout
    approval_printed = False

    while True:
        rule = nc.get_private_endpoint_rule(
            network_connectivity_config_id=outputs.ncc_id,
            private_endpoint_rule_id=outputs.rule_id,
        )
        state = _rule_state(rule)
        info(f"rule {outputs.rule_id} state: {state or 'UNKNOWN'}")

        if state == STATE_ESTABLISHED:
            info("Rule is ESTABLISHED.")
            return
        if state in STATE_TERMINAL_BAD:
            raise SetupError(
                f"Rule is {state}. Recreate it with:\n"
                "  terraform taint databricks_mws_ncc_private_endpoint_rule.aura\n"
                "  terraform apply"
            )

        if state == STATE_PENDING:
            age = _rule_age_days(rule)
            if age is not None and age >= 12:
                info(
                    f"WARNING: rule has been PENDING for {age:.1f} days. "
                    "It expires at 14 days - approve it soon or recreate it."
                )
            if not approval_printed:
                _print_approval_instruction()
                approval_printed = True

        if time.monotonic() >= deadline:
            raise PauseNeeded(
                f"Rule still {state or 'UNKNOWN'} after {timeout}s. Approve the "
                "request in the Aura console, then re-run."
            )
        time.sleep(interval)


def _print_approval_instruction() -> None:
    print(
        "\n  ACTION REQUIRED - approve the private endpoint in the Aura console:\n"
        "    Security -> Network Access -> Pending approvals -> Approve.\n"
        "  Polling will continue in case you have already approved it...\n"
    )


# ---------------------------------------------------------------------------
# Warehouse restart (workspace profile)
# ---------------------------------------------------------------------------

def restart_warehouses(workspace_client, skip: bool) -> None:
    banner("Step 4: Restart running SQL warehouses")
    if skip:
        info("Skipping warehouse restart (--skip-warehouse-restart).")
        return
    running = [
        wh
        for wh in workspace_client.warehouses.list()
        if wh.state is not None and wh.state.value in {"RUNNING", "STARTING"}
    ]
    if not running:
        info("No running SQL warehouses. Serverless jobs get fresh compute anyway.")
        return
    for wh in running:
        info(f"Restarting warehouse '{wh.name}' ({wh.id}) to pick up NCC DNS...")
        workspace_client.warehouses.stop(id=wh.id).result()
        workspace_client.warehouses.start(id=wh.id).result()
        info(f"  '{wh.name}' restarted.")


# ---------------------------------------------------------------------------
# Secret scope (workspace profile)
# ---------------------------------------------------------------------------

def _scope_is_complete(workspace_client) -> bool:
    scopes = {s.name for s in workspace_client.secrets.list_scopes()}
    if SECRET_SCOPE not in scopes:
        return False
    keys = {s.key for s in workspace_client.secrets.list_secrets(SECRET_SCOPE)}
    missing = set(SECRET_KEYS) - keys
    if missing:
        info(f"Scope '{SECRET_SCOPE}' exists but is missing keys: {sorted(missing)}")
        return False
    return True


def ensure_secrets(workspace_client, reset: bool = False) -> None:
    banner("Step 5: Ensure the 'neo4j' secret scope")
    if not reset and _scope_is_complete(workspace_client):
        info(f"Scope '{SECRET_SCOPE}' already has {list(SECRET_KEYS)}. Skipping.")
        return

    required = ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise SetupError(
            "The 'neo4j' secret scope is incomplete and these values are unset: "
            f"{missing}. Set NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD "
            "(and optionally NEO4J_DATABASE) in the repo-root .env, then re-run."
        )

    # Populate the scope directly through the workspace SDK client, which already
    # holds the profile's auth - no bearer token is minted and no subprocess runs.
    from databricks.sdk.errors import ResourceAlreadyExists, ResourceDoesNotExist

    # The env-var check above runs before this delete, so --reset never removes a
    # scope it cannot then repopulate.
    if reset:
        try:
            workspace_client.secrets.delete_scope(scope=SECRET_SCOPE)
            info(f"Deleted existing scope '{SECRET_SCOPE}' (--reset-secret-scope).")
        except ResourceDoesNotExist:
            info(f"Scope '{SECRET_SCOPE}' did not exist; nothing to delete.")

    values = {
        "uri": os.environ["NEO4J_URI"],
        "username": os.environ["NEO4J_USERNAME"],
        "password": os.environ["NEO4J_PASSWORD"],
        "database": os.environ.get("NEO4J_DATABASE", "neo4j"),
    }
    try:
        workspace_client.secrets.create_scope(scope=SECRET_SCOPE)
        info(f"Created scope '{SECRET_SCOPE}'.")
    except ResourceAlreadyExists:
        info(f"Scope '{SECRET_SCOPE}' already exists; updating keys.")
    for key, value in values.items():
        workspace_client.secrets.put_secret(
            scope=SECRET_SCOPE, key=key, string_value=value
        )
        info(f"Set secret '{SECRET_SCOPE}/{key}'.")


# ---------------------------------------------------------------------------
# Validation notebook (workspace profile)
# ---------------------------------------------------------------------------

def import_notebook(workspace_client, local_path: Path) -> str:
    from databricks.sdk.service.workspace import ImportFormat, Language

    remote_path = f"{WORKSPACE_NOTEBOOK_DIR}/{local_path.stem}"
    info(f"Importing {local_path.name} -> {remote_path} (overwrite)")
    workspace_client.workspace.mkdirs(WORKSPACE_NOTEBOOK_DIR)
    content = base64.b64encode(local_path.read_bytes()).decode("ascii")
    workspace_client.workspace.import_(
        path=remote_path,
        content=content,
        format=ImportFormat.SOURCE,
        language=Language.PYTHON,
        overwrite=True,
    )
    return remote_path


def run_validation(workspace_client, notebook: Path, timeout_minutes: int) -> tuple[bool, str]:
    banner("Step 6: Run the validation notebook on serverless")
    from databricks.sdk.service import jobs

    remote_path = import_notebook(workspace_client, notebook)
    info("Submitting a one-time serverless run...")
    waiter = workspace_client.jobs.submit(
        run_name="aura-privatelink-validate",
        tasks=[
            jobs.SubmitTask(
                task_key="validate",
                notebook_task=jobs.NotebookTask(notebook_path=remote_path),
            )
        ],
    )
    run_id = waiter.response.run_id
    info(f"Submitted run {run_id}; waiting up to {timeout_minutes} min...")
    try:
        run = waiter.result(timeout=timedelta(minutes=timeout_minutes))
    except TimeoutError as exc:
        raise PauseNeeded(
            f"Validation run {run_id} did not finish within {timeout_minutes} min "
            "(cold serverless pool or queueing). Check the Databricks Jobs UI, "
            "then re-run this command."
        ) from exc
    result_state = run.state.result_state.value if run.state and run.state.result_state else "UNKNOWN"
    output_text = _collect_run_output(workspace_client, run)

    passed = result_state == "SUCCESS"
    info(f"Run finished: {result_state}")
    if output_text:
        print("\n--- notebook output ---")
        print(output_text)
        print("--- end output ---")
    return passed, output_text


def _collect_run_output(workspace_client, run) -> str:
    from databricks.sdk.errors import DatabricksError

    parts: list[str] = []
    for task in run.tasks or []:
        try:
            out = workspace_client.jobs.get_run_output(run_id=task.run_id)
        except DatabricksError as exc:
            parts.append(f"(could not read output for task {task.task_key}: {exc})")
            continue
        if out.error:
            parts.append(f"ERROR: {out.error}")
        if out.error_trace:
            parts.append(out.error_trace)
        if out.notebook_output and out.notebook_output.result:
            parts.append(out.notebook_output.result)
    return "\n".join(p for p in parts if p)


def report_routing_hosts(output_text: str) -> None:
    hosts = sorted(set(ROUTING_HOST_RE.findall(output_text)))
    if not hosts:
        return
    banner("ACTION REQUIRED - unresolved Neo4j routing hosts")
    print(
        "The validation run failed because these Aura routing hostnames do not\n"
        "resolve through NCC DNS yet. Add them to aura_extra_domain_names in\n"
        f"{TERRAFORM_DIR / 'terraform.tfvars'}, then re-run:\n"
    )
    formatted = ", ".join(f'"{h}"' for h in hosts)
    print(f'  aura_extra_domain_names = [{formatted}]\n')


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def make_account_client(profile: str | None):
    from databricks.sdk import AccountClient

    if not profile:
        raise SetupError(
            "An account-console profile is required to poll the NCC rule.\n"
            "Configure one, then pass --account-profile <name>:\n"
            "  databricks auth login --host https://accounts.azuredatabricks.net "
            "--account-id <account-id>"
        )
    return AccountClient(profile=profile)


def make_workspace_client(profile: str | None):
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient(profile=profile) if profile else WorkspaceClient()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    notebook = Path(args.notebook) if args.notebook else DEFAULT_NOTEBOOK
    if not notebook.exists():
        raise SetupError(f"Validation notebook not found: {notebook}")

    outputs = read_terraform_outputs() if args.no_apply else apply_stack()
    info(f"NCC id  : {outputs.ncc_id}")
    info(f"Rule id : {outputs.rule_id}")

    account_client = make_account_client(args.account_profile)
    poll_rule(account_client, outputs, args.poll_timeout, args.poll_interval)

    workspace_client = make_workspace_client(args.workspace_profile)
    restart_warehouses(workspace_client, args.skip_warehouse_restart)
    ensure_secrets(workspace_client, reset=args.reset_secret_scope)

    passed, output_text = run_validation(workspace_client, notebook, args.run_timeout)
    if passed:
        banner("SUCCESS - Private Link path validated end-to-end")
        return 0

    report_routing_hosts(output_text)
    banner("Validation FAILED - see output above")
    return 1


def cmd_teardown(args: argparse.Namespace) -> int:
    banner("Teardown: destroy the databricks-ncc Terraform stack")
    info("This removes the NCC, the Aura private endpoint rule, and the workspace binding.")
    info(f"Stack: {TERRAFORM_DIR}")
    if not args.yes:
        try:
            reply = input("\n  Run `terraform destroy` on this stack? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in {"y", "yes"}:
            info("Aborted. Nothing was destroyed.")
            return 0

    init = _terraform(["init", "-input=false", "-no-color"], capture=True)
    if init.returncode != 0:
        raise SetupError(f"terraform init failed:\n{init.stdout}\n{init.stderr}")

    destroy = _terraform(["destroy", "-input=false", "-no-color", "-auto-approve"])
    if destroy.returncode != 0:
        raise SetupError("terraform destroy failed. See the output above.")

    banner("Teardown complete - Databricks side removed")
    print(
        "The Databricks NCC, private endpoint rule, and workspace binding are gone.\n"
        "Two Aura-side items have no API and remain for you to clean up manually:\n"
        "  1. Aura console -> Network Access: remove the now-orphaned private endpoint\n"
        "     approval.\n"
        "  2. Aura console -> Network Access: optionally remove the Databricks-managed\n"
        "     subscription from 'Target Azure Subscription IDs' if no longer needed.\n"
        "The 'neo4j' secret scope and the imported validation notebook are left in the\n"
        "workspace; delete them by hand if you want a full reset.\n"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="automate.py",
        description="Orchestrate the Databricks Serverless + Neo4j Aura Private Link setup.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Drive the setup to a passing validation run (re-entrant).")
    run.add_argument(
        "--account-profile",
        help="Databricks CLI profile for the account console (accounts.azuredatabricks.net).",
    )
    run.add_argument(
        "--workspace-profile",
        default="azure-rk-knight",
        help="Databricks CLI profile for the target workspace (default: azure-rk-knight).",
    )
    run.add_argument(
        "--no-apply",
        action="store_true",
        help="Skip `terraform apply`; only read `terraform output -json`.",
    )
    run.add_argument(
        "--notebook",
        help=f"Validation notebook to run (default: {DEFAULT_NOTEBOOK.relative_to(REPO_ROOT)}).",
    )
    run.add_argument("--poll-timeout", type=int, default=600, help="Seconds to wait for ESTABLISHED.")
    run.add_argument("--poll-interval", type=int, default=15, help="Seconds between rule polls.")
    run.add_argument(
        "--run-timeout",
        type=int,
        default=30,
        help="Minutes to wait for the validation run to finish (default: 30).",
    )
    run.add_argument(
        "--skip-warehouse-restart",
        action="store_true",
        help="Do not stop/start running SQL warehouses (useful on re-runs after success).",
    )
    run.add_argument(
        "--reset-secret-scope",
        action="store_true",
        help="Delete the 'neo4j' secret scope before recreating it (resolves a "
        "scope holding another instance's credentials).",
    )
    run.set_defaults(func=cmd_run)

    teardown = sub.add_parser(
        "teardown",
        help="Destroy the databricks-ncc Terraform stack (NCC, PE rule, binding).",
    )
    teardown.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt (for headless / CI use).",
    )
    teardown.set_defaults(func=cmd_teardown)
    return parser


def main() -> int:
    # Load Aura credentials from the repo-root .env so NEO4J_* need not be
    # exported by hand. Real environment variables take precedence.
    load_dotenv(REPO_ROOT / ".env")
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except PauseNeeded as exc:
        banner("PAUSED - action needed, then re-run this command")
        print(f"  {exc}")
        return 2
    except SetupError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
