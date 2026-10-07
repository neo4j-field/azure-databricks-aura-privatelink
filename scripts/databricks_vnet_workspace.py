#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "python-dotenv>=1.0",
# ]
# ///
"""
databricks_vnet_workspace.py - create a VNet-injected Azure Databricks workspace.

The Private Link path needs a classic cluster in a VNet you own. A workspace in a
Databricks-managed VNet cannot take a private endpoint. This script puts the workspace in
your VNet, so scripts/private_link.py can add the private endpoint to the same VNet.

It adds three things to an existing VNet, then creates the workspace:

  nsg-dbx            network security group shared by both Databricks subnets
  snet-dbx-host      host (public) subnet, delegated to Microsoft.Databricks/workspaces
  snet-dbx-container container (private) subnet, delegated the same way
  workspace          Premium, secure cluster connectivity on (no public IPs on nodes)

The private endpoint subnet stays separate and is not delegated, so the VNet needs a third
subnet for it. `up` leaves any existing subnet alone.

Settings come from .env, the same file private_link.py reads:

  VNET             VNet to inject into (required)
  VNET_RG          its resource group (defaults to RG)
  RG               resource group for the workspace (defaults to VNET_RG)
  WORKSPACE_NAME   workspace to create (default dbx-aura-pl-test)

Commands (both re-entrant, both take --dry-run):
  up     Create the NSG, the two delegated subnets, and the workspace. Each step is
         skipped when it already exists.
  down   Delete the workspace, then the two subnets and the NSG. It refuses to delete a
         workspace that is not injected into this VNet.

Usage:
  uv run scripts/databricks_vnet_workspace.py up --dry-run
  uv run scripts/databricks_vnet_workspace.py up
  uv run scripts/private_link.py run
  uv run scripts/databricks_vnet_workspace.py down
"""

import argparse
import sys
from dataclasses import dataclass

from dotenv import dotenv_values

import private_link
from private_link import (
    REPO_ROOT,
    AzError,
    az_exists,
    az_tsv,
    banner,
    change,
    info,
    row,
)

DEFAULT_WORKSPACE = "dbx-aura-pl-test"
NSG = "nsg-dbx"
HOST_SUBNET = "snet-dbx-host"
CONTAINER_SUBNET = "snet-dbx-container"
DELEGATION = "Microsoft.Databricks/workspaces"


@dataclass
class Settings:
    vnet: str
    vnet_resource_group: str
    resource_group: str
    workspace: str
    host_cidr: str
    container_cidr: str


def load_settings(args: argparse.Namespace) -> Settings:
    config = dotenv_values(REPO_ROOT / ".env").get
    vnet = config("VNET")
    if not vnet:
        raise SystemExit("error: VNET is not set. Set VNET and VNET_RG in .env.")
    vnet_rg = config("VNET_RG") or config("RG")
    if not vnet_rg:
        raise SystemExit("error: VNET_RG is not set. Set it in .env.")
    return Settings(
        vnet=vnet,
        vnet_resource_group=vnet_rg,
        resource_group=config("RG") or vnet_rg,
        workspace=config("WORKSPACE_NAME") or DEFAULT_WORKSPACE,
        host_cidr=args.host_cidr,
        container_cidr=args.container_cidr,
    )


def vnet_id(s: Settings) -> str:
    return az_tsv("network", "vnet", "show", "-g", s.vnet_resource_group,
                  "-n", s.vnet, "--query", "id")  # fmt: skip


def ensure_extension() -> None:
    if az_exists("extension", "show", "-n", "databricks"):
        row("OK", "az extension databricks")
        return
    row("CREATE", "az extension databricks")
    change("extension", "add", "-n", "databricks", "-y")


def ensure_nsg(s: Settings, location: str) -> None:
    rg = s.vnet_resource_group
    if az_exists("network", "nsg", "show", "-g", rg, "-n", NSG):
        row("OK", f"network security group {NSG}")
        return
    row("CREATE", f"network security group {NSG}", location)
    change("network", "nsg", "create", "-g", rg, "-n", NSG, "-l", location, "-o", "none")


def ensure_subnet(s: Settings, name: str, cidr: str) -> None:
    rg = s.vnet_resource_group
    shown = ("network", "vnet", "subnet", "show", "-g", rg, "--vnet-name", s.vnet,
             "-n", name)  # fmt: skip
    if az_exists(*shown):
        delegation = az_tsv(*shown, "--query", "delegations[0].serviceName")
        if delegation != DELEGATION:
            row("ERROR", f"subnet {name}", f"exists but is not delegated to {DELEGATION}")
            raise AzError(f"subnet {name} exists without the Databricks delegation")
        row("OK", f"subnet {name}")
        return
    row("CREATE", f"subnet {name}", cidr)
    change(
        "network", "vnet", "subnet", "create", "-g", rg, "--vnet-name", s.vnet,
        "-n", name, "--address-prefixes", cidr, "--delegations", DELEGATION,
        "--network-security-group", NSG, "-o", "none",
    )  # fmt: skip


def injected_vnet(s: Settings) -> str:
    """The VNet ID the workspace is injected into, or an empty string."""
    return az_tsv("databricks", "workspace", "show", "-g", s.resource_group,
                  "-n", s.workspace, "--query",
                  "parameters.customVirtualNetworkId.value")  # fmt: skip


def ensure_workspace(s: Settings, location: str, vnet: str) -> None:
    if az_exists("databricks", "workspace", "show", "-g", s.resource_group,
                 "-n", s.workspace):  # fmt: skip
        if injected_vnet(s).lower() != vnet.lower():
            row("ERROR", f"workspace {s.workspace}", "exists but is in another VNet")
            raise AzError(f"workspace {s.workspace} is not injected into {s.vnet}")
        row("OK", f"workspace {s.workspace}")
        return
    row("CREATE", f"workspace {s.workspace}", "Premium, secure cluster connectivity")
    info("this takes several minutes")
    change(
        "databricks", "workspace", "create", "-g", s.resource_group,
        "-n", s.workspace, "-l", location, "--sku", "premium",
        "--vnet", vnet, "--public-subnet", HOST_SUBNET,
        "--private-subnet", CONTAINER_SUBNET, "--enable-no-public-ip", "true",
        "-o", "none",
    )  # fmt: skip


def cmd_up(args: argparse.Namespace) -> int:
    s = load_settings(args)
    banner(f"Create VNet-injected workspace {s.workspace}")
    info(f"subscription: {az_tsv('account', 'show', '--query', 'id')}")
    info(f"VNet: {s.vnet} ({s.vnet_resource_group})")
    location = az_tsv("network", "vnet", "show", "-g", s.vnet_resource_group,
                      "-n", s.vnet, "--query", "location")  # fmt: skip
    ensure_extension()
    ensure_nsg(s, location)
    ensure_subnet(s, HOST_SUBNET, s.host_cidr)
    ensure_subnet(s, CONTAINER_SUBNET, s.container_cidr)
    ensure_workspace(s, location, vnet_id(s))
    banner("Next")
    info("Add these to .env, then run: uv run scripts/private_link.py run")
    info(f'WORKSPACE_NAME="{s.workspace}"')
    info('PE_SUBNET="<a subnet that is not delegated, such as snet-pe>"')
    info("Create a Databricks CLI profile for the new workspace URL:")
    url = ""
    if not private_link.DRY_RUN:
        url = az_tsv("databricks", "workspace", "show", "-g", s.resource_group,
                     "-n", s.workspace, "--query", "workspaceUrl")  # fmt: skip
    info(f"databricks auth login --host https://{url or '<workspace-url>'}")
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    s = load_settings(args)
    banner(f"Delete workspace {s.workspace}")
    shown = ("databricks", "workspace", "show", "-g", s.resource_group, "-n", s.workspace)
    if az_exists(*shown):
        if injected_vnet(s).lower() != vnet_id(s).lower():
            raise AzError(f"workspace {s.workspace} is not injected into {s.vnet}")
        row("DELETE", f"workspace {s.workspace}")
        change("databricks", "workspace", "delete", "-g", s.resource_group,
               "-n", s.workspace, "--yes")  # fmt: skip
    else:
        row("OK", f"workspace {s.workspace}", "not found")
    for name in (HOST_SUBNET, CONTAINER_SUBNET):
        if az_exists("network", "vnet", "subnet", "show", "-g", s.vnet_resource_group,
                     "--vnet-name", s.vnet, "-n", name):  # fmt: skip
            row("DELETE", f"subnet {name}")
            change("network", "vnet", "subnet", "delete", "-g", s.vnet_resource_group,
                   "--vnet-name", s.vnet, "-n", name)  # fmt: skip
    if az_exists("network", "nsg", "show", "-g", s.vnet_resource_group, "-n", NSG):
        row("DELETE", f"network security group {NSG}")
        change("network", "nsg", "delete", "-g", s.vnet_resource_group, "-n", NSG)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a VNet-injected Azure Databricks workspace."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("up", cmd_up), ("down", cmd_down)):
        cmd = sub.add_parser(name)
        cmd.add_argument("--dry-run", action="store_true",
                         help="print the az commands without running them")  # fmt: skip
        cmd.add_argument("--host-cidr", default="10.10.10.0/24",
                         help="host subnet range (default %(default)s)")  # fmt: skip
        cmd.add_argument("--container-cidr", default="10.10.11.0/24",
                         help="container subnet range (default %(default)s)")  # fmt: skip
        cmd.set_defaults(func=func)
    args = parser.parse_args()
    private_link.DRY_RUN = args.dry_run
    try:
        return args.func(args)
    except AzError as err:
        print(f"\nERROR\n  {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
