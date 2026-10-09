# Databricks notebook source
# MAGIC %md
# MAGIC # Neo4j Aura Private Link Connectivity Validation
# MAGIC
# MAGIC End-to-end check that a classic Azure Databricks cluster reaches Neo4j Aura over an Azure
# MAGIC private endpoint. Run it on a classic cluster in a VNet-injected workspace. Serverless
# MAGIC compute cannot use this path. Use `ncc-notebooks/` for serverless.
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - The private endpoint is approved in the Aura console.
# MAGIC - The `databases.neo4j.io` private DNS zone is linked to the workspace VNet and holds an
# MAGIC   A record for the Aura instance. See `docs/setup-private-link-manual.md` or
# MAGIC   `docs/setup-private-link-terraform.md`.
# MAGIC - The cluster was restarted after the last DNS change.
# MAGIC - The Databricks secret scope `neo4j` holds the keys `uri`, `username`, and `password`.
# MAGIC   An optional `database` key overrides the default database `neo4j`.
# MAGIC
# MAGIC **What it checks**
# MAGIC 1. The Aura host resolves to a private address. With the optional `expected_pe_ip`
# MAGIC    widget, it resolves to that exact endpoint IP.
# MAGIC 2. TCP reaches the Bolt port.
# MAGIC 3. Every routing host that Aura advertises resolves privately. A routing host without a
# MAGIC    DNS record fails here, and the notebook prints the command that adds it.
# MAGIC 4. A Bolt query succeeds through the normal driver, with no resolver workaround.
# MAGIC
# MAGIC **Widgets**
# MAGIC - `expected_pe_ip`: Optional. The private endpoint IP, from `private_link.py status` or the
# MAGIC   endpoint NIC in the Azure portal.
# MAGIC - `use_resolver`: Default `false`. Set `true` to map routing hosts back to the instance host
# MAGIC   in the driver. This hides missing routing-host records, so use it only to compare.

# COMMAND ----------

# MAGIC %pip install --quiet neo4j==5.* tenacity==9.*
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md ## 1. Load credentials and widgets

# COMMAND ----------

dbutils.widgets.text("expected_pe_ip", "")
dbutils.widgets.dropdown("use_resolver", "false", ["false", "true"])

SECRET_SCOPE     = "neo4j"
REQUIRED_SECRETS = ("uri", "username", "password")

try:
    SECRET_KEYS = {s.key for s in dbutils.secrets.list(SECRET_SCOPE)}
except Exception as exc:
    raise RuntimeError(
        f"ERROR: secret scope '{SECRET_SCOPE}' does not exist or is not readable. "
        "Create it and add the keys uri, username and password (optional: database)."
    ) from exc

missing = [k for k in REQUIRED_SECRETS if k not in SECRET_KEYS]
if missing:
    raise RuntimeError(
        f"ERROR: missing secret(s) in scope '{SECRET_SCOPE}': {', '.join(missing)}. "
        "Add them with `databricks secrets put-secret`."
    )

NEO4J_URI      = dbutils.secrets.get(scope=SECRET_SCOPE, key="uri")
NEO4J_USER     = dbutils.secrets.get(scope=SECRET_SCOPE, key="username")
NEO4J_PASSWORD = dbutils.secrets.get(scope=SECRET_SCOPE, key="password")
NEO4J_DATABASE = (
    dbutils.secrets.get(scope="neo4j", key="database")
    if "database" in SECRET_KEYS
    else "neo4j"
)
EXPECTED_PE_IP = dbutils.widgets.get("expected_pe_ip").strip()
USE_RESOLVER   = dbutils.widgets.get("use_resolver") == "true"

print(f"URI scheme/host : {NEO4J_URI.split('@')[-1]}")
print(f"User            : {NEO4J_USER}")
print(f"Database        : {NEO4J_DATABASE}")
print(f"Expected PE IP  : {EXPECTED_PE_IP or '(not set, checking for any private IP)'}")
print(f"Use resolver    : {USE_RESOLVER}")

# COMMAND ----------

# MAGIC %md ## 2. DNS check: the Aura host must resolve to the private endpoint

# COMMAND ----------

import ipaddress
import socket
from urllib.parse import urlparse

parsed = urlparse(NEO4J_URI)
host = parsed.hostname
port = parsed.port or 7687


def resolve_ipv4(name):
    infos = socket.getaddrinfo(name, port, socket.AF_INET, socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


ips = resolve_ipv4(host)
print(f"{host} -> {', '.join(ips)}")

public_ips = [ip for ip in ips if not ipaddress.ip_address(ip).is_private]
assert not public_ips, (
    f"DNS resolved to a public IP ({', '.join(public_ips)}). Private Link is NOT in use. "
    "Check that the private DNS zone is linked to the workspace VNet, that it holds an A record "
    "for the instance, and that the cluster was restarted after the last DNS change."
)
if EXPECTED_PE_IP:
    assert ips == [EXPECTED_PE_IP], (
        f"DNS resolved to {', '.join(ips)}, but the private endpoint IP is {EXPECTED_PE_IP}. "
        "Update the A record to the current endpoint IP."
    )
    print(f"OK: resolves to the private endpoint IP {EXPECTED_PE_IP}.")
else:
    print("OK: resolves to private address space. Set expected_pe_ip to also pin the exact IP.")

# COMMAND ----------

# MAGIC %md ## 3. TCP reachability on the Bolt port

# COMMAND ----------

with socket.create_connection((host, port), timeout=10) as s:
    print(f"TCP connect succeeded: {s.getpeername()}")

# COMMAND ----------

# MAGIC %md ## 4. Routing hosts must resolve privately
# MAGIC
# MAGIC Aura VDC advertises routing hosts shaped like `p-<aura-instance-id>-<suffix>.<orch>.neo4j.io`.
# MAGIC They sit in a different DNS zone from the instance host. A client without a resolver
# MAGIC workaround fails with `Cannot resolve address p-...` when one has no record.
# MAGIC
# MAGIC This cell opens a direct `bolt+s` connection, which needs no routing, and reads the
# MAGIC routing table. Then it resolves every advertised host.

# COMMAND ----------

from neo4j import GraphDatabase

direct_driver = GraphDatabase.driver(
    f"bolt+s://{host}:{port}",
    auth=(NEO4J_USER, NEO4J_PASSWORD),
    connection_timeout=10,
)
with direct_driver as direct, direct.session(database=NEO4J_DATABASE) as session:
    routing = session.run(
        "CALL dbms.routing.getRoutingTable({}, $db) YIELD servers", db=NEO4J_DATABASE
    ).single()

routing_hosts = sorted(
    {addr.rsplit(":", 1)[0] for server in routing["servers"] for addr in server["addresses"]}
)


def check_routing_host(name):
    try:
        found = resolve_ipv4(name)
    except socket.gaierror:
        return "FAIL", "does not resolve"
    public = [ip for ip in found if not ipaddress.ip_address(ip).is_private]
    if public:
        return "FAIL", f"public {', '.join(public)}"
    if EXPECTED_PE_IP and found != [EXPECTED_PE_IP]:
        return "FAIL", f"{', '.join(found)} is not the endpoint IP"
    return "PASS", ", ".join(found)


results = {name: check_routing_host(name) for name in routing_hosts}
for name, (status, detail) in results.items():
    print(f"{status}  {name}  {detail}")

failed = [name for name, (status, _) in results.items() if status == "FAIL"]
if failed and USE_RESOLVER:
    print(
        f"\nWARN: {len(failed)} routing host(s) fail DNS. use_resolver is true, so the driver "
        "maps them to the instance host and the query below can still pass. Clients without "
        "that resolver will fail."
    )
else:
    assert not failed, (
        f"{len(failed)} routing host(s) do not resolve to the private endpoint. Add a record "
        "for each from a machine with the repo checked out:\n"
        + "\n".join(f'  uv run scripts/private_link.py add-routing-host "{name}"' for name in failed)
        + "\nThen restart the cluster and run this notebook again."
    )
    print("OK: every advertised routing host resolves privately.")

# COMMAND ----------

# MAGIC %md ## 5. Bolt+TLS connect and run a query

# COMMAND ----------

import re

from neo4j.exceptions import ServiceUnavailable, TransientError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Aura VDC advertises routing hosts shaped like p-<aura-instance-id>-<suffix>.<orch>.neo4j.io;
# the instance id is the first label of the host.
ROUTING_HOST_PATTERN = re.compile(rf"^p-{re.escape(host.split('.')[0])}-.*\.neo4j\.io$")


def aura_private_resolver(address):
    # Maps routing hosts back to the instance host. Only installed when use_resolver is
    # true, because it hides routing-host DNS records that are missing.
    mapped_host = host if ROUTING_HOST_PATTERN.match(address.host) else address.host
    if mapped_host != address.host:
        print(f"Resolver alias: {address.host}:{address.port} -> {mapped_host}:{address.port}")
    return [(mapped_host, address.port)]


driver = GraphDatabase.driver(
    NEO4J_URI,
    auth=(NEO4J_USER, NEO4J_PASSWORD),
    resolver=aura_private_resolver if USE_RESOLVER else None,
    connection_timeout=10,
    max_connection_lifetime=300,
)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((ServiceUnavailable, TransientError)),
    reraise=True,
)
def count_nodes():
    with driver.session(database=NEO4J_DATABASE) as session:
        return session.execute_read(
            lambda tx: tx.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        )


node_count = count_nodes()
print(f"Connected successfully. Current node count: {node_count}")

# COMMAND ----------

# MAGIC %md ## 6. Verify driver routing info

# COMMAND ----------

driver.verify_connectivity()
print("Driver verify_connectivity() OK.")

# COMMAND ----------

driver.close()
resolver_note = "with the resolver workaround" if USE_RESOLVER else "with plain DNS"
print(f"Validation complete. The Private Link path is working end-to-end {resolver_note}.")
