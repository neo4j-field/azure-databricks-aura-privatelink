#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "python-dotenv>=1.0",
# ]
# ///
"""
private_link_testbed.py - build a throwaway Azure stand-in for Aura, to test private_link.py.

Aura's Private Link service lives in Neo4j's subscription and needs a human to approve
the connection. This script builds a stand-in so the full life cycle of
scripts/private_link.py can run without Aura:

  consumer VNet  (vnet-consumer)  holds the private endpoint subnet and an optional VM
  provider VNet  (vnet-provider)  holds an internal load balancer and a Private Link
                                  service with manual approval, like Aura

Everything lives in one resource group tagged purpose=aura-privatelink-testbed. `down`
refuses to delete a resource group without that tag.

Commands:
  up [--vm]   Create the resource group, both VNets, the load balancer, and the PLS.
              --vm adds a small Ubuntu VM in the consumer VNet to resolve DNS from.
  approve     Approve every pending connection on the PLS. This stands in for the
              Aura console step.
  env         Print the .env lines that private_link.py reads.
  down        Delete the resource group and everything in it.

Usage:
  uv run scripts/private_link_testbed.py up --vm
  uv run scripts/private_link_testbed.py env >> .env
  uv run scripts/private_link.py create
  uv run scripts/private_link_testbed.py approve
  uv run scripts/private_link.py dns
  uv run scripts/private_link_testbed.py down
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from private_link import AzError, az, az_exists, az_json, az_tsv, banner, info

TAG = "purpose=aura-privatelink-testbed"
CONSUMER_VNET = "vnet-consumer"
PE_SUBNET = "snet-pe"
VM_SUBNET = "snet-vm"
PROVIDER_VNET = "vnet-provider"
LB = "lb-provider"
LB_FRONTEND = "fe"
PLS = "pls-test"
VM = "vm-test"
PE_NAME = "pe-testdb01-test"
INSTANCE_ID = "testdb01"


def create_vnet(rg: str, vnet: str, prefix: str, subnets: dict[str, str]) -> None:
    first, *rest = subnets.items()
    az(
        "network", "vnet", "create", "-g", rg, "-n", vnet,
        "--address-prefixes", prefix,
        "--subnet-name", first[0], "--subnet-prefixes", first[1], "-o", "none",
    )  # fmt: skip
    for name, cidr in rest:
        extra = ["--disable-private-link-service-network-policies", "true"]
        az(
            "network", "vnet", "subnet", "create", "-g", rg, "--vnet-name", vnet,
            "-n", name, "--address-prefixes", cidr,
            *(extra if name == "snet-pls" else []), "-o", "none",
        )  # fmt: skip


def create_vm(rg: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        key = Path(tmp) / "key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            check=True,
        )
        az(
            "vm", "create", "-g", rg, "-n", VM, "--image", "Ubuntu2204",
            "--size", "Standard_B1s", "--vnet-name", CONSUMER_VNET,
            "--subnet", VM_SUBNET, "--public-ip-address", "", "--nsg", "",
            "--admin-username", "azureuser",
            "--ssh-key-values", f"{key}.pub", "-o", "none",
        )  # fmt: skip


def cmd_up(args: argparse.Namespace) -> int:
    rg = args.resource_group
    banner(f"Testbed up: {rg}")
    sub_id = az_tsv("account", "show", "--query", "id")
    info(f"subscription: {sub_id}")
    if az_exists("group", "show", "-n", rg):
        raise AzError(f"resource group {rg} already exists, run `down` first")
    az("group", "create", "-n", rg, "-l", args.location, "--tags", TAG, "-o", "none")
    create_vnet(rg, CONSUMER_VNET, "10.10.0.0/16", {PE_SUBNET: "10.10.1.0/24", VM_SUBNET: "10.10.2.0/24"})  # fmt: skip
    create_vnet(rg, PROVIDER_VNET, "10.20.0.0/16", {"snet-backend": "10.20.1.0/24", "snet-pls": "10.20.2.0/24"})  # fmt: skip
    az(
        "network", "lb", "create", "-g", rg, "-n", LB, "--sku", "Standard",
        "--vnet-name", PROVIDER_VNET, "--subnet", "snet-backend",
        "--frontend-ip-name", LB_FRONTEND, "--backend-pool-name", "be", "-o", "none",
    )  # fmt: skip
    az(
        "network", "private-link-service", "create", "-g", rg, "-n", PLS,
        "-l", args.location, "--vnet-name", PROVIDER_VNET, "--subnet", "snet-pls",
        "--lb-name", LB, "--lb-frontend-ip-configs", LB_FRONTEND,
        "--visibility", sub_id, "-o", "none",
    )  # fmt: skip
    if args.vm:
        create_vm(rg)
    banner("Ready")
    info("Run: eval \"$(uv run scripts/private_link_testbed.py env)\"")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    rg = args.resource_group
    banner("Approve pending PLS connections")
    connections = az_json(
        "network", "private-link-service", "show", "-g", rg, "-n", PLS,
        "--query", "privateEndpointConnections[].{name:name, "
        "status:privateLinkServiceConnectionState.status}",
    )  # fmt: skip
    pending = [c["name"] for c in connections if c["status"] == "Pending"]
    if not pending:
        info(f"no pending connections: {connections}")
        return 1
    for name in pending:
        az(
            "network", "private-link-service", "connection", "update",
            "-g", rg, "--service-name", PLS, "-n", name,
            "--connection-status", "Approved", "-o", "none",
        )  # fmt: skip
    return 0


def cmd_env(args: argparse.Namespace) -> int:
    rg = args.resource_group
    alias = az_tsv(
        "network", "private-link-service", "show", "-g", rg, "-n", PLS,
        "--query", "alias",
    )  # fmt: skip
    exports = {
        "PE_RG": rg,
        "VNET": CONSUMER_VNET,
        "VNET_RG": rg,
        "PE_SUBNET": PE_SUBNET,
        "PE_NAME": PE_NAME,
        "AURA_PLS_ALIAS": alias,
        "AURA_INSTANCE_ID": INSTANCE_ID,
    }
    for key, value in exports.items():
        print(f'{key}="{value}"')
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    rg = args.resource_group
    banner(f"Testbed down: {rg}")
    if not az_exists("group", "show", "-n", rg):
        info(f"resource group {rg} not found, nothing to do")
        return 0
    tag = az_tsv("group", "show", "-n", rg, "--query", "tags.purpose")
    if tag != TAG.split("=", 1)[1]:
        raise AzError(f"{rg} is not tagged {TAG}, refusing to delete it")
    az("group", "delete", "-n", rg, "--yes")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Throwaway stand-in for Aura Private Link.")
    parser.add_argument("--resource-group", default="rg-aura-pl-test")
    parser.add_argument("--location", default="eastus")
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("up")
    up.add_argument("--vm", action="store_true", help="add a VM to resolve DNS from")
    up.set_defaults(func=cmd_up)
    sub.add_parser("approve").set_defaults(func=cmd_approve)
    sub.add_parser("env").set_defaults(func=cmd_env)
    sub.add_parser("down").set_defaults(func=cmd_down)
    args = parser.parse_args()
    try:
        return args.func(args)
    except AzError as err:
        print(f"\nERROR\n  {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
