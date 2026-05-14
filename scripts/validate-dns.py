#!/usr/bin/env python3
"""
Standalone DNS validation for the Aura hostname.

Run this from inside a Databricks serverless notebook (or a Databricks job)
to confirm that the Aura Private URI resolves to a private IP via the
NCC-managed DNS.

Usage:
    python validate-dns.py d48d6199.databases.neo4j.io
"""

import ipaddress
import socket
import sys


def is_private(ip_str: str) -> bool:
    return ipaddress.ip_address(ip_str).is_private


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: validate-dns.py <hostname>", file=sys.stderr)
        return 2

    host = sys.argv[1]
    try:
        ip = socket.gethostbyname(host)
    except socket.gaierror as e:
        print(f"DNS resolution failed for {host}: {e}", file=sys.stderr)
        return 1

    print(f"{host} -> {ip}")

    if is_private(ip):
        print("OK: resolves to a private address. Private Link path is active.")
        return 0

    print(
        "FAIL: resolves to a public address. Private Link is NOT in use.\n"
        "Check that the NCC private endpoint rule includes `domain_names`\n"
        "and that the rule status is ESTABLISHED.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
