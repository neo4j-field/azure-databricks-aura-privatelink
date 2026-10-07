#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "python-dotenv>=1.0",
# ]
# ///
"""
private_link.py - set up Azure Private Link to Neo4j Aura with the Azure CLI, no Terraform.

This wraps the commands in docs/setup-private-link-manual.md. It creates a private
endpoint in your VNet that targets the Aura Private Link service, then wires the
private DNS so the Aura hostname resolves to the endpoint NIC. Every `az` call is
printed to stderr, so the output doubles as a transcript of the manual steps.

It matches the single-VNet mode of the Terraform stack (`manage_private_dns = true`).
For a central hub DNS, follow docs/shared/private-dns-central.md instead.

Auth is the Azure CLI session:
  az login --tenant <tenant-id>
  az account set --subscription <subscription-id>

Settings come only from the repo-root .env file. There are no flags for them and
exported shell variables are ignored: RG, VNET, VNET_RG, PE_SUBNET, PE_NAME,
AURA_PLS_ALIAS, AURA_INSTANCE_ID, WORKSPACE_NAME, REQUEST_MESSAGE.
Only AURA_PLS_ALIAS has no default. The script fills in the rest when unset:
  AURA_INSTANCE_ID  the first label of NEO4J_URI
  VNET, VNET_RG     the custom VNet of a VNet-injected Databricks workspace. Set
                    WORKSPACE_NAME to pick one, otherwise the script
                    lists the workspaces in the subscription and asks.
  RG                VNET_RG
  PE_SUBNET         a subnet in VNET that is not delegated. It asks if there are several,
                    and marks the likeliest one as the default.
  PE_NAME           an existing pe-<instance-id>-* endpoint in RG, else
                    pe-<instance-id>-<VNet region>
When it cannot ask because stdin is not a terminal, it stops and lists the options.

Every command is re-entrant. It inspects what exists and only changes the difference.

Commands:
  create             Create the private endpoint (doc Step 2).
  status [--wait]    Show the connection state and endpoint IP (doc Step 4).
  dns                Create the zone, VNet link, zone group, and A record (doc Steps 5, 6).
  add-routing-host   Add an A record for a p-<aura-instance-id>-<suffix>.<orch>.neo4j.io host.
  verify             Compare DNS records to the endpoint IP. Add --vm NAME to resolve
                     from a VM, and --bolt to run a connectivity test with neo4j-cli.
                     It checks the zones this script's link names mark, so it covers
                     the single-VNet mode only.
  run                create, wait for the Aura approval, then dns.
  destroy            Delete the endpoint and the DNS objects this script created.

--dry-run (create, dns, add-routing-host, run, destroy) changes nothing. It prints what
exists and what would be created, updated, or deleted:
  OK      exists and correct         CREATE  would be created
  UPDATE  exists but differs         DELETE  would be deleted
  WAIT    waiting on the Aura console   CHECK   cannot be judged until the endpoint exists
  KEEP    kept, another link uses it    ERROR   blocks the setup

Exit codes: 0 done, 1 error or failed check (a dry run with an ERROR row included),
2 paused on a human step in the Aura console. Run again after the step.

Usage:
  uv run scripts/private_link.py run --dry-run
  uv run scripts/private_link.py run
  uv run scripts/private_link.py verify --bolt
"""

import argparse
import ipaddress
import json
import shlex
import shutil
import socket
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent
ZONE = "databases.neo4j.io"
ENV_KEYS = {
    "resource_group": "RG",
    "vnet": "VNET",
    "vnet_resource_group": "VNET_RG",
    "subnet": "PE_SUBNET",
    "name": "PE_NAME",
    "pls_alias": "AURA_PLS_ALIAS",
    "instance_id": "AURA_INSTANCE_ID",
}
WORKSPACE_TYPE = "Microsoft.Databricks/workspaces"
RESERVED_SUBNETS = {"GatewaySubnet", "AzureFirewallSubnet", "AzureBastionSubnet"}
DNS_TTL = "30"
BOLT_PORT = 7687
STATUS_APPROVED = "Approved"
STATUS_BAD = {"Rejected", "Disconnected"}
EXIT_PAUSED = 2
PLS_ALIAS_SUFFIX = ".azure.privatelinkservice"
CHANGING = {"CREATE", "UPDATE", "DELETE", "WAIT", "ERROR"}

DRY_RUN = False


class AzError(Exception):
    """An `az` call failed."""


class PauseNeeded(Exception):
    """The flow is waiting on a human action in the Aura console."""


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass
class Settings:
    resource_group: str | None
    vnet: str | None
    vnet_resource_group: str | None
    subnet: str | None
    name: str | None
    pls_alias: str | None
    instance_id: str | None
    request_message: str
    workspace: str | None = None
    notes: list[str] = field(default_factory=list)

    def require(self, *fields: str) -> None:
        missing = [f for f in fields if not getattr(self, f)]
        if missing:
            keys = ", ".join(ENV_KEYS[f] for f in missing)
            raise SystemExit(f"error: missing required setting in .env: {keys}")

    def found(self, label: str, value: str, source: str) -> None:
        """Record a setting the script filled in, so the run can print it."""
        self.notes.append(f"{label:<10} {value}  ({source})")

    @property
    def vnet_rg(self) -> str | None:
        return self.vnet_resource_group or self.resource_group

    @property
    def vnet_link(self) -> str:
        return f"{self.name}-vnet-link"

    @property
    def orch_link(self) -> str:
        return f"{self.name}-orch-link"

    @property
    def instance_host(self) -> str:
        return f"{self.instance_id}.{ZONE}"


@dataclass
class BoltOptions:
    uri: str
    credential: str | None
    env_file: str | None


# ---------------------------------------------------------------------------
# Output and Azure CLI helpers
# ---------------------------------------------------------------------------

PLAN: list[tuple[str, str]] = []


def info(msg: str) -> None:
    print(f"  {msg}")


def banner(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def row(state: str, resource: str, detail: str = "") -> None:
    """Print one line of the plan and remember it for the dry-run summary."""
    PLAN.append((state, resource))
    print(f"  {state:<7} {resource}" + (f"  {detail}" if detail else ""))


def az(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run one `az` command, echoing it to stderr first."""
    echo = "  $ az " + " ".join(shlex.quote(a) for a in args)
    print(echo, file=sys.stderr)
    proc = subprocess.run(
        ["az", *args, "--only-show-errors"],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise AzError(f"az {' '.join(args[:3])} failed:\n{proc.stderr.strip()}")
    return proc


def change(*args: str) -> None:
    """Run an `az` command that changes Azure, or only print it in a dry run."""
    if DRY_RUN:
        print("  [dry-run] az " + " ".join(shlex.quote(a) for a in args), file=sys.stderr)
        return
    az(*args)


def az_json(*args: str):
    return json.loads(az(*args, "-o", "json").stdout)


def az_tsv(*args: str) -> str:
    return az(*args, "-o", "tsv").stdout.strip()


def az_exists(*args: str) -> bool:
    return az(*args, "-o", "none", check=False).returncode == 0


# ---------------------------------------------------------------------------
# Discovery: fill in settings left unset
# ---------------------------------------------------------------------------


@dataclass
class Workspace:
    name: str
    resource_group: str
    vnet_id: str | None


def pick(what: str, labels: list[str], setting: str, default: int | None = None) -> int:
    """Return the index of the chosen label. Ask on a terminal, otherwise stop.

    An empty answer takes `default` when one is given.
    """
    if len(labels) == 1:
        return 0
    listing = "\n".join(f"    {i}. {label}" for i, label in enumerate(labels, 1))
    if not sys.stdin.isatty():
        raise SystemExit(
            f"error: {what} is not set. Set {setting} in .env. Options:\n{listing}"
        )
    print(f"\n  Choose {what}:\n{listing}")
    hint = f" [{default + 1}]" if default is not None else ""
    while True:
        try:
            answer = input(f"  Enter a number{hint}: ").strip()
        except EOFError:
            raise SystemExit(f"error: no answer for {what}. Set {setting} in .env.") from None
        if not answer and default is not None:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(labels):
            return int(answer) - 1


def instance_id_from_uri(uri: str | None) -> str | None:
    """abcd1234 from neo4j+s://abcd1234.databases.neo4j.io, else None."""
    host = urlparse(uri).hostname if uri else None
    if host and host.endswith(f".{ZONE}"):
        return host.removesuffix(f".{ZONE}")
    return None


def parse_vnet_id(resource_id: str) -> tuple[str, str]:
    """Return (resource group, name) from a VNet resource ID."""
    parts = resource_id.strip("/").split("/")
    group = parts[[p.lower() for p in parts].index("resourcegroups") + 1]
    return group, parts[-1]


def list_workspaces() -> list[Workspace]:
    """Databricks workspaces in the subscription, with the VNet each one injects into."""
    found = []
    for item in az_json("resource", "list", "--resource-type", WORKSPACE_TYPE,
                        "--query", "[].{id:id, name:name, rg:resourceGroup}"):  # fmt: skip
        vnet_id = az_tsv("resource", "show", "--ids", item["id"], "--query",
                         "properties.parameters.customVirtualNetworkId.value")  # fmt: skip
        found.append(Workspace(item["name"], item["rg"], vnet_id or None))
    return found


def choose_workspace(s: Settings) -> Workspace:
    workspaces = list_workspaces()
    if s.workspace:
        matches = [w for w in workspaces if w.name == s.workspace]
        if not matches:
            raise SystemExit(
                f"error: no Databricks workspace named {s.workspace} in this subscription"
            )
    else:
        matches = [w for w in workspaces if w.vnet_id]
        if not matches:
            raise SystemExit(
                f"error: {len(workspaces)} Databricks workspace(s) found and none is "
                "VNet-injected. A workspace in a Databricks-managed VNet cannot take a "
                "private endpoint, so use docs/setup-ncc-manual.md. For a VNet that is "
                "not a workspace VNet, set VNET and VNET_RG in .env."
            )
    labels = [f"{w.name}  ({w.resource_group})" for w in matches]
    chosen = matches[pick("a Databricks workspace", labels, "WORKSPACE_NAME or VNET")]
    if not chosen.vnet_id:
        raise SystemExit(
            f"error: workspace {chosen.name} is not VNet-injected. It uses a "
            "Databricks-managed VNet, so use docs/setup-ncc-manual.md."
        )
    return chosen


def discover_network(s: Settings) -> None:
    """Fill vnet, vnet_resource_group, and resource_group from a workspace."""
    if s.vnet:
        if not s.resource_group and s.vnet_resource_group:
            s.resource_group = s.vnet_resource_group
            s.found("RG", s.resource_group, "same as VNET_RG")
        return
    workspace = choose_workspace(s)
    group, name = parse_vnet_id(workspace.vnet_id)
    source = f"workspace {workspace.name}"
    s.vnet, s.vnet_resource_group = name, group
    s.found("VNET", name, source)
    s.found("VNET_RG", group, source)
    if not s.resource_group:
        s.resource_group = group
        s.found("RG", group, "same as VNET_RG")
    if not s.workspace:
        s.found("WORKSPACE_NAME", workspace.name, "chosen")


AZURE_RESERVED_IPS = 5  # Azure holds back 5 addresses in every subnet


def subnet_prefixes(subnet: dict) -> list[str]:
    return subnet.get("addressPrefixes") or [subnet["addressPrefix"]]


def subnet_nics(subnet: dict) -> int:
    """NICs in the subnet that are not private endpoints, such as VM NICs."""
    return len(subnet.get("ipConfigurations") or []) - len(subnet.get("privateEndpoints") or [])


def subnet_label(subnet: dict, suggested: bool) -> str:
    """One line that tells subnets apart: CIDR, what lives in it, and free IPs."""
    prefixes = subnet_prefixes(subnet)
    endpoints = len(subnet.get("privateEndpoints") or [])
    nics = subnet_nics(subnet)
    used = len(subnet.get("ipConfigurations") or [])
    size = sum(ipaddress.ip_network(p).num_addresses for p in prefixes)
    facts = [
        f"{endpoints} private endpoint{'' if endpoints == 1 else 's'}",
        f"{nics} other NIC{'' if nics == 1 else 's'}",
        f"{size - AZURE_RESERVED_IPS - used} IPs free",
    ]
    for key, name in (("networkSecurityGroup", "NSG"), ("routeTable", "route table")):
        if subnet.get(key):
            facts.append(f"{name} {subnet[key]['id'].rsplit('/', 1)[-1]}")
    marker = "  <- suggested" if suggested else ""
    return f"{subnet['name']}  {', '.join(prefixes)}  ({', '.join(facts)}){marker}"


def suggest_subnet(subnets: list[dict]) -> int | None:
    """Index of the subnet most likely meant for the endpoint, or None.

    The one that already holds private endpoints wins. Failing that, the only
    subnet with no other NICs in it, since a subnet with VMs is a workload subnet.
    """
    endpoints = [len(x.get("privateEndpoints") or []) for x in subnets]
    if max(endpoints) > 0:
        return endpoints.index(max(endpoints))
    empty = [i for i, x in enumerate(subnets) if not x.get("ipConfigurations")]
    return empty[0] if len(empty) == 1 else None


def discover_subnet(s: Settings) -> None:
    """Pick a subnet that can hold the endpoint NIC."""
    if s.subnet or not (s.vnet and s.vnet_rg):
        return
    subnets = az_json("network", "vnet", "subnet", "list",
                      "--resource-group", s.vnet_rg, "--vnet-name", s.vnet)  # fmt: skip
    usable = [
        x for x in subnets
        if not x.get("delegations") and x["name"] not in RESERVED_SUBNETS
    ]  # fmt: skip
    if not usable:
        raise SystemExit(
            f"error: every subnet in {s.vnet} is delegated or reserved, so none can hold "
            "a private endpoint. Create a subnet, then set PE_SUBNET in .env."
        )
    suggested = suggest_subnet(usable)
    labels = [subnet_label(x, i == suggested) for i, x in enumerate(usable)]
    s.subnet = usable[pick("a subnet for the endpoint", labels, "PE_SUBNET", suggested)]["name"]
    s.found("PE_SUBNET", s.subnet, "not delegated")


def discover_endpoint(s: Settings) -> None:
    """Reuse an existing pe-<instance-id>-* endpoint, else derive a name from the VNet region."""
    if s.name and s.resource_group:
        return
    if not s.instance_id:
        return
    prefix = f"pe-{s.instance_id}-"
    scope = ["--resource-group", s.resource_group] if s.resource_group else []
    proc = az("network", "private-endpoint", "list", *scope, "--query",
              f"[?starts_with(name, '{prefix}')].{{name:name, rg:resourceGroup}}",
              "-o", "json", check=False)  # fmt: skip
    matches = json.loads(proc.stdout) if proc.returncode == 0 else []
    if s.name:
        matches = [m for m in matches if m["name"] == s.name]
    if matches:
        labels = [f"{m['name']}  ({m['rg']})" for m in matches]
        chosen = matches[pick("a private endpoint", labels, "PE_NAME and RG")]
        if not s.name:
            s.name = chosen["name"]
            s.found("PE_NAME", s.name, "existing endpoint")
        if not s.resource_group:
            s.resource_group = chosen["rg"]
            s.found("RG", s.resource_group, "existing endpoint")
    elif not s.name and s.vnet and s.vnet_rg:
        region = az_tsv("network", "vnet", "show", "--resource-group", s.vnet_rg,
                        "--name", s.vnet, "--query", "location")  # fmt: skip
        s.name = prefix + region
        s.found("PE_NAME", s.name, "instance id and VNet region")


def discover(s: Settings, command: str) -> None:
    """Fill every unset setting that Azure can answer, then print what it found."""
    if command in {"create", "dns", "add-routing-host", "run"}:
        discover_network(s)
    if command in {"create", "run"}:
        discover_subnet(s)
    discover_endpoint(s)
    if s.notes:
        banner("Discovered settings")
        for line in s.notes:
            info(line)
        info("Put these in .env to skip the lookup.")


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def preflight(s: Settings) -> bool:
    """Check what the endpoint needs before it is created. Returns False on a blocker."""
    s.require("resource_group", "vnet", "subnet", "name", "pls_alias")
    proc = az("account", "show", "-o", "json", check=False)
    if proc.returncode != 0:
        row("ERROR", "azure login", "run: az login --tenant <tenant-id>")
        return False
    account = json.loads(proc.stdout)
    row("OK", f"subscription {account['name']}", account["id"])

    ok = True
    if az_exists("group", "show", "-n", s.resource_group):
        row("OK", f"resource group {s.resource_group}")
    else:
        row("ERROR", f"resource group {s.resource_group}", "does not exist")
        ok = False
    subnet = ("network", "vnet", "subnet", "show", "-g", s.vnet_rg,
              "--vnet-name", s.vnet, "-n", s.subnet)  # fmt: skip
    if az_exists(*subnet):
        row("OK", f"subnet {s.vnet}/{s.subnet}")
    else:
        row("ERROR", f"subnet {s.vnet}/{s.subnet}", f"not found in {s.vnet_rg}")
        ok = False
    if s.pls_alias.endswith(PLS_ALIAS_SUFFIX):
        row("OK", "Aura PLS alias", s.pls_alias)
    else:
        row("ERROR", "Aura PLS alias", f"does not end in {PLS_ALIAS_SUFFIX}")
        ok = False
    return ok


# ---------------------------------------------------------------------------
# Private endpoint
# ---------------------------------------------------------------------------


def get_endpoint(s: Settings) -> dict | None:
    proc = az(
        "network", "private-endpoint", "show",
        "--name", s.name, "--resource-group", s.resource_group,
        "-o", "json", check=False,
    )  # fmt: skip
    return json.loads(proc.stdout) if proc.returncode == 0 else None


def connection_state(endpoint: dict) -> tuple[str, str]:
    """Return (status, description) of the endpoint's Private Link connection."""
    connections = (
        endpoint.get("manualPrivateLinkServiceConnections")
        or endpoint.get("privateLinkServiceConnections")
        or []
    )
    if not connections:
        return "Unknown", ""
    state = connections[0].get("privateLinkServiceConnectionState", {})
    return state.get("status", "Unknown"), state.get("description", "")


def endpoint_ip(endpoint: dict) -> str:
    nic_id = endpoint["networkInterfaces"][0]["id"]
    return az_tsv(
        "network", "nic", "show", "--ids", nic_id,
        "--query", "ipConfigurations[0].privateIPAddress",
    )  # fmt: skip


def create_endpoint(s: Settings) -> dict | None:
    """Create the endpoint. Returns None in a dry run when it does not exist yet."""
    s.require("resource_group", "vnet", "subnet", "name", "pls_alias")
    existing = get_endpoint(s)
    if existing:
        status, _ = connection_state(existing)
        row("OK", f"private endpoint {s.name}", f"exists, connection {status}")
        return existing
    row("CREATE", f"private endpoint {s.name}", f"-> {s.pls_alias}")
    if DRY_RUN:
        return None
    subnet_id = az_tsv(
        "network", "vnet", "subnet", "show", "--resource-group", s.vnet_rg,
        "--vnet-name", s.vnet, "--name", s.subnet, "--query", "id",
    )  # fmt: skip
    return az_json(
        "network", "private-endpoint", "create",
        "--name", s.name, "--resource-group", s.resource_group,
        "--subnet", subnet_id,
        "--nic-name", f"{s.name}-nic",
        "--connection-name", f"{s.name}-conn",
        "--private-connection-resource-id", s.pls_alias,
        "--manual-request", "true",
        "--request-message", s.request_message,
    )  # fmt: skip


def approval_instructions() -> str:
    return (
        "Approve the request in the Aura console:\n"
        "  Project settings > Security & Networking > Private endpoints >\n"
        "  Edit network access configuration > Step 3 of 4: Endpoint "
        "Connection Requests > Accept.\n"
        "Then run this command again."
    )


def wait_approved(s: Settings, timeout: int, interval: int) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        endpoint = get_endpoint(s)
        if endpoint is None:
            raise AzError(f"endpoint {s.name} does not exist, run `create` first")
        status, description = connection_state(endpoint)
        info(f"connection status: {status} {description}".rstrip())
        if status == STATUS_APPROVED:
            return endpoint
        if status in STATUS_BAD:
            raise AzError(
                f"connection is {status}. Delete the endpoint with `destroy`, "
                "then run `create` again."
            )
        if time.monotonic() >= deadline:
            raise PauseNeeded(approval_instructions())
        time.sleep(interval)


def report_approval(s: Settings, endpoint: dict | None) -> None:
    """Dry-run view of the approval step. It never waits."""
    if endpoint is None:
        row("WAIT", "Aura approval", "after the endpoint is created")
        return
    status, _ = connection_state(endpoint)
    if status == STATUS_APPROVED:
        row("OK", "Aura approval", STATUS_APPROVED)
    elif status in STATUS_BAD:
        row("ERROR", "Aura approval", f"{status}, destroy and create again")
    else:
        row("WAIT", "Aura approval", f"{status}, accept it in the Aura console")


# ---------------------------------------------------------------------------
# Private DNS
# ---------------------------------------------------------------------------


def vnet_id(s: Settings) -> str:
    return az_tsv(
        "network", "vnet", "show", "--resource-group", s.vnet_rg,
        "--name", s.vnet, "--query", "id",
    )  # fmt: skip


def ensure_zone(rg: str, zone: str) -> None:
    if az_exists("network", "private-dns", "zone", "show", "-g", rg, "-n", zone):
        row("OK", f"private DNS zone {zone}")
        return
    row("CREATE", f"private DNS zone {zone}")
    change("network", "private-dns", "zone", "create", "-g", rg, "-n", zone, "-o", "none")


def ensure_link(rg: str, zone: str, link: str, vnet: str) -> None:
    if az_exists(
        "network", "private-dns", "link", "vnet", "show",
        "-g", rg, "-z", zone, "-n", link,
    ):  # fmt: skip
        row("OK", f"VNet link {link}", f"on {zone}")
        return
    row("CREATE", f"VNet link {link}", f"on {zone}")
    change(
        "network", "private-dns", "link", "vnet", "create",
        "-g", rg, "-z", zone, "-n", link,
        "--virtual-network", vnet, "--registration-enabled", "false", "-o", "none",
    )  # fmt: skip


def ensure_zone_group(s: Settings, endpoint: dict | None) -> None:
    if endpoint is None:
        row("CREATE", "zone group default", "after the endpoint is created")
        return
    # `dns-zone-group show` exits 0 for a missing group, so list and compare names.
    groups = az_json(
        "network", "private-endpoint", "dns-zone-group", "list",
        "-g", s.resource_group, "--endpoint-name", s.name, "--query", "[].name",
    )  # fmt: skip
    if "default" in groups:
        row("OK", "zone group default")
        return
    row("CREATE", "zone group default")
    change(
        "network", "private-endpoint", "dns-zone-group", "create",
        "-g", s.resource_group, "--endpoint-name", s.name, "-n", "default",
        "--zone-name", ZONE, "--private-dns-zone", ZONE, "-o", "none",
    )  # fmt: skip


def ensure_a_record(rg: str, zone: str, record: str, ip: str | None) -> None:
    """Make the record set hold exactly one address, the endpoint IP.

    `ip` is None only in a dry run when the endpoint does not exist yet.
    """
    fqdn = f"{record}.{zone}"
    proc = az(
        "network", "private-dns", "record-set", "a", "show",
        "-g", rg, "-z", zone, "-n", record, "-o", "json", check=False,
    )  # fmt: skip
    if proc.returncode != 0:
        row("CREATE", f"A record {fqdn}", f"-> {ip or '<endpoint IP>'}")
        if ip is None:
            return
        change(
            "network", "private-dns", "record-set", "a", "create",
            "-g", rg, "-z", zone, "-n", record, "--ttl", DNS_TTL, "-o", "none",
        )  # fmt: skip
        current: list[str] = []
    else:
        current = [r["ipv4Address"] for r in json.loads(proc.stdout)["aRecords"]]
        if ip is None:
            row("CHECK", f"A record {fqdn}", f"points at {current}, endpoint not created")
            return
        if current == [ip]:
            row("OK", f"A record {fqdn}", f"-> {ip}")
            return
        row("UPDATE", f"A record {fqdn}", f"{current} -> {ip}")
    for stale in (a for a in current if a != ip):
        change(
            "network", "private-dns", "record-set", "a", "remove-record",
            "-g", rg, "-z", zone, "--record-set-name", record,
            "--ipv4-address", stale, "--keep-empty-record-set", "-o", "none",
        )  # fmt: skip
    change(
        "network", "private-dns", "record-set", "a", "add-record",
        "-g", rg, "-z", zone, "--record-set-name", record,
        "--ipv4-address", ip, "-o", "none",
    )  # fmt: skip


def current_endpoint(s: Settings) -> tuple[dict | None, str | None]:
    """Return the endpoint and its IP. Only a dry run tolerates a missing endpoint."""
    endpoint = get_endpoint(s)
    if endpoint is None:
        if not DRY_RUN:
            raise AzError(f"endpoint {s.name} does not exist, run `create` first")
        return None, None
    return endpoint, endpoint_ip(endpoint)


def setup_dns(s: Settings) -> None:
    s.require("resource_group", "vnet", "name", "instance_id")
    endpoint, ip = current_endpoint(s)
    if ip:
        info(f"endpoint private IP: {ip}")
    ensure_zone(s.resource_group, ZONE)
    ensure_link(s.resource_group, ZONE, s.vnet_link, vnet_id(s))
    ensure_zone_group(s, endpoint)
    ensure_a_record(s.resource_group, ZONE, s.instance_id, ip)


def split_routing_host(host: str) -> tuple[str, str]:
    """Split p-<aura-instance-id>-<suffix>.<orch>.neo4j.io into (record label, zone)."""
    label, _, zone = host.strip().rstrip(".").partition(".")
    if not label or not zone.endswith(".neo4j.io") or zone == ZONE:
        raise SystemExit(
            f"error: {host} is not a routing host. Expected "
            "p-<aura-instance-id>-<suffix>.<orch>.neo4j.io, not under databases.neo4j.io."
        )
    return label, zone


def add_routing_host(s: Settings, host: str) -> None:
    s.require("resource_group", "vnet", "name")
    label, zone = split_routing_host(host)
    _, ip = current_endpoint(s)
    ensure_zone(s.resource_group, zone)
    ensure_link(s.resource_group, zone, s.orch_link, vnet_id(s))
    ensure_a_record(s.resource_group, zone, label, ip)


def our_zones(s: Settings) -> list[str]:
    """Zones in the resource group that carry a link this script created."""
    names = {s.vnet_link, s.orch_link}
    zones = az_json(
        "network", "private-dns", "zone", "list",
        "-g", s.resource_group, "--query", "[].name",
    )  # fmt: skip
    return [
        z
        for z in zones
        if names & set(az_json(
            "network", "private-dns", "link", "vnet", "list",
            "-g", s.resource_group, "-z", z, "--query", "[].name",
        ))
    ]  # fmt: skip


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


def zone_records(s: Settings, zone: str) -> dict[str, list[str]]:
    rows = az_json(
        "network", "private-dns", "record-set", "a", "list",
        "-g", s.resource_group, "-z", zone,
        "--query", "[].{name:name, ips:aRecords[].ipv4Address}",
    )  # fmt: skip
    return {r["name"]: r["ips"] for r in rows}


def run_on_vm(script: str, vm: str, vm_rg: str) -> str:
    out = az_json(
        "vm", "run-command", "invoke", "-g", vm_rg, "-n", vm,
        "--command-id", "RunShellScript", "--scripts", script,
    )  # fmt: skip
    return out["value"][0]["message"]


def result(good: bool, text: str) -> bool:
    print(f"  {'OK  ' if good else 'FAIL'} {text}")
    return good


def bolt_test(uri: str, credential: str | None, env_file: str | None, pe_ip: str) -> bool:
    """Resolve the host, open a TCP connection, then run a query with neo4j-cli."""
    parsed = urlparse(uri)
    host, port = parsed.hostname, parsed.port or BOLT_PORT
    if not host:
        return result(False, f"cannot read a host from {uri}")
    try:
        addresses = sorted({a[4][0] for a in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except socket.gaierror as err:
        return result(False, f"bolt: {host} does not resolve here: {err}")
    private = all(ipaddress.ip_address(a).is_private for a in addresses)
    if pe_ip in addresses:
        result(True, f"bolt: {host} -> {', '.join(addresses)} matches the endpoint")
    elif private:
        result(True, f"bolt: {host} -> {', '.join(addresses)} is a private address")
    else:
        print(f"  WARN bolt: {host} -> {', '.join(addresses)} is public, so this")
        print("       machine is not on the private path. The result below tests the")
        print("       public path. Run it from a VM in a linked VNet for the private one.")
    try:
        with socket.create_connection((host, port), timeout=5):
            result(True, f"bolt: tcp {host}:{port} is reachable")
    except OSError as err:
        return result(False, f"bolt: tcp {host}:{port} is not reachable: {err}")

    if not shutil.which("neo4j-cli"):
        return result(False, "bolt: neo4j-cli is not on PATH")
    cmd = ["neo4j-cli", "query", "RETURN 1 AS ok", "--uri", uri, "--format", "toon"]
    if credential:
        cmd += ["--credential", credential]
    if env_file:
        cmd += ["--env", env_file]
    print("  $ " + " ".join(shlex.quote(c) for c in cmd), file=sys.stderr)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            stdin=subprocess.DEVNULL, check=False,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        return result(False, "bolt: neo4j-cli query timed out after 60s")
    if proc.returncode != 0:
        lines = [
            line.strip()
            for line in (proc.stderr or proc.stdout).splitlines()
            if line.strip() and not line.startswith("Full output saved")
        ]
        return result(
            False, "bolt: neo4j-cli query failed: " + (lines[0] if lines else "")
        )
    return result(True, "bolt: neo4j-cli query RETURN 1 succeeded")


def verify(s: Settings, vm: str | None, vm_rg: str | None, bolt: BoltOptions | None) -> bool:
    s.require("resource_group", "name", "instance_id")
    endpoint = get_endpoint(s)
    if endpoint is None:
        raise AzError(f"endpoint {s.name} does not exist")
    ip = endpoint_ip(endpoint)
    status, _ = connection_state(endpoint)
    ok = status == STATUS_APPROVED
    print(f"  connection {status}, endpoint IP {ip}")

    hosts: list[str] = []
    for zone in our_zones(s):
        for record, ips in zone_records(s, zone).items():
            fqdn = f"{record}.{zone}"
            hosts.append(fqdn)
            ok &= result(ips == [ip], f"{fqdn} -> {', '.join(ips)}")
    if s.instance_host not in hosts:
        ok = result(False, f"{s.instance_host} has no record")

    vm_rg = vm_rg or s.resource_group
    if vm:
        for host in hosts:
            message = run_on_vm(f"getent hosts {host}", vm, vm_rg)
            answer = " ".join(message.split("[stdout]")[-1].split()[:2])
            ok &= result(ip in message, f"{vm} resolves {host}: {answer}")
    if bolt:
        ok &= bolt_test(bolt.uri, bolt.credential, bolt.env_file, ip)
        if vm:
            host = urlparse(bolt.uri).hostname
            message = run_on_vm(
                f"timeout 5 bash -c '</dev/tcp/{host}/{BOLT_PORT}' && echo TCP_OK || echo TCP_FAIL",
                vm, vm_rg,
            )  # fmt: skip
            ok &= result("TCP_OK" in message, f"bolt: {vm} reaches {host}:{BOLT_PORT}")
    return ok


# ---------------------------------------------------------------------------
# Destroy
# ---------------------------------------------------------------------------


def destroy(s: Settings) -> None:
    s.require("resource_group", "name")
    zones = our_zones(s)  # find them before the endpoint goes away
    ours = {s.vnet_link, s.orch_link}
    if get_endpoint(s):
        row("DELETE", f"private endpoint {s.name}")
        change(
            "network", "private-endpoint", "delete",
            "--name", s.name, "--resource-group", s.resource_group,
        )  # fmt: skip
    else:
        row("OK", f"private endpoint {s.name}", "already gone")
    for zone in zones:
        links = az_json(
            "network", "private-dns", "link", "vnet", "list",
            "-g", s.resource_group, "-z", zone, "--query", "[].name",
        )  # fmt: skip
        for link in (name for name in links if name in ours):
            row("DELETE", f"VNet link {link}", f"on {zone}")
            change(
                "network", "private-dns", "link", "vnet", "delete",
                "-g", s.resource_group, "-z", zone, "-n", link, "--yes",
            )  # fmt: skip
        remaining = [name for name in links if name not in ours]
        if remaining:
            row("KEEP", f"private DNS zone {zone}", f"still linked: {remaining}")
            continue
        row("DELETE", f"private DNS zone {zone}", "and its records")
        change(
            "network", "private-dns", "zone", "delete",
            "-g", s.resource_group, "-n", zone, "--yes",
        )  # fmt: skip


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def dry_run_summary() -> None:
    banner("Dry run summary")
    counts = Counter(state for state, _ in PLAN)
    info(", ".join(f"{n} {state}" for state, n in sorted(counts.items())))
    if "ERROR" in counts:
        info("Nothing was changed. Fix the ERROR lines above, then run again.")
    elif CHANGING & set(counts):
        info("Nothing was changed. Run again without --dry-run to apply it.")
    else:
        info("Nothing to do. The setup matches.")


def cmd_create(args: argparse.Namespace, s: Settings) -> int:
    banner("Create private endpoint")
    if not preflight(s) and not DRY_RUN:
        raise AzError("preflight failed, fix the ERROR lines above")
    if PLAN and any(state == "ERROR" for state, _ in PLAN):
        return 0
    create_endpoint(s)
    if not DRY_RUN:
        info("Next: approve the request in the Aura console, then `status --wait`.")
    return 0


def cmd_status(args: argparse.Namespace, s: Settings) -> int:
    s.require("resource_group", "name")
    banner("Connection status")
    if args.wait:
        endpoint = wait_approved(s, args.timeout, args.interval)
    else:
        endpoint = get_endpoint(s)
        if endpoint is None:
            raise AzError(f"endpoint {s.name} does not exist")
        status, description = connection_state(endpoint)
        info(f"connection status: {status} {description}".rstrip())
    info(f"endpoint private IP: {endpoint_ip(endpoint)}")
    return 0


def cmd_dns(args: argparse.Namespace, s: Settings) -> int:
    banner("Private DNS")
    setup_dns(s)
    return 0


def cmd_add_routing_host(args: argparse.Namespace, s: Settings) -> int:
    banner(f"Routing host {args.host}")
    add_routing_host(s, args.host)
    return 0


def cmd_verify(args: argparse.Namespace, s: Settings) -> int:
    banner("Verify")
    bolt = None
    if args.bolt:
        s.require("instance_id")
        bolt = BoltOptions(
            args.bolt_uri or f"neo4j+s://{s.instance_host}",
            args.bolt_credential,
            args.bolt_env,
        )
    ok = verify(s, args.vm, args.vm_resource_group, bolt)
    print("\n  PASS" if ok else "\n  FAIL")
    return 0 if ok else 1


def cmd_run(args: argparse.Namespace, s: Settings) -> int:
    banner("1. Preflight and private endpoint")
    if not preflight(s):
        if not DRY_RUN:
            raise AzError("preflight failed, fix the ERROR lines above")
        return 0
    endpoint = create_endpoint(s)
    banner("2. Aura approval")
    if DRY_RUN:
        report_approval(s, endpoint)
    else:
        wait_approved(s, args.timeout, args.interval)
    banner("3. Private DNS")
    setup_dns(s)
    if not DRY_RUN:
        banner("Done")
        info("Test it: uv run scripts/private_link.py verify --bolt")
        info("If a Bolt connection reports `Cannot resolve address p-...neo4j.io`,")
        info("run `add-routing-host <host>` for each host named in the error.")
    return 0


def cmd_destroy(args: argparse.Namespace, s: Settings) -> int:
    banner("Destroy")
    destroy(s)
    if not DRY_RUN:
        info("Now remove the orphaned approval in the Aura console.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Azure Private Link to Neo4j Aura via the Azure CLI. "
        "Settings come from the repo-root .env file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    common = argparse.ArgumentParser(add_help=False)

    dry = argparse.ArgumentParser(add_help=False)
    dry.add_argument(
        "--dry-run",
        action="store_true",
        help="change nothing, show what exists and what would change",
    )

    wait = argparse.ArgumentParser(add_help=False)
    wait.add_argument("--timeout", type=int, default=600, help="seconds to wait")
    wait.add_argument("--interval", type=int, default=15, help="seconds between polls")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create", parents=[common, dry]).set_defaults(func=cmd_create)
    p = sub.add_parser("status", parents=[common, wait])
    p.add_argument("--wait", action="store_true", help="wait for Approved")
    p.set_defaults(func=cmd_status)
    sub.add_parser("dns", parents=[common, dry]).set_defaults(func=cmd_dns)
    p = sub.add_parser("add-routing-host", parents=[common, dry])
    p.add_argument("host", help="p-<aura-instance-id>-<suffix>.<orch>.neo4j.io")
    p.set_defaults(func=cmd_add_routing_host)
    p = sub.add_parser("verify", parents=[common])
    p.add_argument("--vm", help="resolve each host from this VM via run-command")
    p.add_argument("--vm-resource-group", help="defaults to RG")
    p.add_argument("--bolt", action="store_true", help="run a connectivity test with neo4j-cli")
    p.add_argument("--bolt-uri", help="default: neo4j+s://<instance-id>.databases.neo4j.io")
    p.add_argument("--bolt-credential", help="neo4j-cli stored credential name")
    p.add_argument("--bolt-env", help="path to a .env file with NEO4J_USERNAME and NEO4J_PASSWORD")
    p.set_defaults(func=cmd_verify)
    sub.add_parser("run", parents=[common, wait, dry]).set_defaults(func=cmd_run)
    sub.add_parser("destroy", parents=[common, dry]).set_defaults(func=cmd_destroy)
    return parser


def main() -> int:
    global DRY_RUN
    config = dotenv_values(REPO_ROOT / ".env").get
    args = build_parser().parse_args()
    DRY_RUN = getattr(args, "dry_run", False)
    settings = Settings(
        resource_group=config("RG"),
        vnet=config("VNET"),
        vnet_resource_group=config("VNET_RG"),
        subnet=config("PE_SUBNET"),
        name=config("PE_NAME"),
        pls_alias=config("AURA_PLS_ALIAS"),
        instance_id=config("AURA_INSTANCE_ID") or instance_id_from_uri(config("NEO4J_URI")),
        request_message=config("REQUEST_MESSAGE") or "Neo4j Aura Private Link",
        workspace=config("WORKSPACE_NAME"),
    )
    try:
        discover(settings, args.command)
        code = args.func(args, settings)
    except PauseNeeded as pause:
        print(f"\nPAUSED\n  {pause}")
        return EXIT_PAUSED
    except AzError as err:
        print(f"\nERROR\n  {err}", file=sys.stderr)
        return 1
    if DRY_RUN and PLAN:
        dry_run_summary()
        if any(state == "ERROR" for state, _ in PLAN):
            return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
