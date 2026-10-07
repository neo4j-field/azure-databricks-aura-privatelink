# Databricks notebook source
# MAGIC %md
# MAGIC # Neo4j Aura NCC Connectivity Validation
# MAGIC
# MAGIC End-to-end check that Azure Databricks serverless compute reaches Neo4j Aura over a
# MAGIC private endpoint managed by a Network Connectivity Configuration (NCC).
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - The NCC is attached to this workspace and its private endpoint rule reads `ESTABLISHED`.
# MAGIC   See docs/setup-ncc-manual.md or docs/setup-ncc-terraform.md.
# MAGIC - The Databricks secret scope `neo4j` holds the keys `uri`, `username`, and `password`.
# MAGIC   An optional `database` key overrides the default database `neo4j`.
# MAGIC
# MAGIC The driver in step 4 maps Aura routing hosts (`p-<aura-instance-id>-*.neo4j.io`) to the
# MAGIC instance host, so this notebook passes without routing-host DNS entries.

# COMMAND ----------

# MAGIC %pip install --quiet neo4j==5.* tenacity==9.*
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md ## 1. Load credentials from Databricks Secrets

# COMMAND ----------

NEO4J_URI      = dbutils.secrets.get(scope="neo4j", key="uri")
NEO4J_USER     = dbutils.secrets.get(scope="neo4j", key="username")
NEO4J_PASSWORD = dbutils.secrets.get(scope="neo4j", key="password")
SECRET_KEYS    = {s.key for s in dbutils.secrets.list("neo4j")}
NEO4J_DATABASE = (
    dbutils.secrets.get(scope="neo4j", key="database")
    if "database" in SECRET_KEYS
    else "neo4j"
)

print(f"URI scheme/host : {NEO4J_URI.split('@')[-1]}")
print(f"User            : {NEO4J_USER}")
print(f"Database        : {NEO4J_DATABASE}")

# COMMAND ----------

# MAGIC %md ## 2. DNS sanity check: must resolve to a PRIVATE IP

# COMMAND ----------

import ipaddress
import socket
from urllib.parse import urlparse

host = urlparse(NEO4J_URI).hostname
ip = socket.gethostbyname(host)
print(f"{host} -> {ip}")

assert ipaddress.ip_address(ip).is_private, (
    f"DNS resolved to a public IP ({ip}). The private path is NOT in use. "
    "Check that the NCC private endpoint rule includes domain_names and is ESTABLISHED."
)
print("OK: resolves to private address space.")

# COMMAND ----------

# MAGIC %md ## 3. TCP reachability on Bolt port 7687

# COMMAND ----------

s = socket.create_connection((host, 7687), timeout=10)
print(f"TCP connect succeeded to {host}:7687")
s.close()

# COMMAND ----------

# MAGIC %md ## 4. Bolt+TLS connect and run a query

# COMMAND ----------

import re

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, TransientError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Derive the routing-host pattern from the connection URI (populated from the
# `neo4j` secret scope / .env), so this notebook follows the configured Aura
# instance instead of a pinned instance id. Aura VDC advertises routing hosts shaped
# like p-<aura-instance-id>-<suffix>.<orch>.neo4j.io; the instance id is the first
# label of the host.
dbid = host.split(".")[0]
ROUTING_HOST_PATTERN = re.compile(rf"^p-{re.escape(dbid)}-.*\.neo4j\.io$")

def aura_private_resolver(address):
    # Aura VDC can return p-*.neo4j.io addresses in the routing table. If those
    # names do not resolve privately, map them to the Aura instance hostname,
    # which the NCC rule or the private DNS zone already resolves.
    mapped_host = host if ROUTING_HOST_PATTERN.match(address.host) else address.host
    if mapped_host != address.host:
        print(f"Resolver alias: {address.host}:{address.port} -> {mapped_host}:{address.port}")
    return [(mapped_host, address.port)]

driver = GraphDatabase.driver(
    NEO4J_URI,
    auth=(NEO4J_USER, NEO4J_PASSWORD),
    resolver=aura_private_resolver,
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

driver.close()
print("Validation complete. NCC private path is working end-to-end.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Debug DNS (optional)
# MAGIC
# MAGIC Run this cell only if a check above fails or a client reports an unresolved host.
# MAGIC It does not affect the pass/fail result. To check a routing host, set `ROUTING_HOST`
# MAGIC in `.env` and re-run `scripts/create-secret-scope.sh`. The cell then reads it from the
# MAGIC `routing_host` secret.

# COMMAND ----------

debug_hosts = [host]
if "routing_host" in SECRET_KEYS:
    debug_hosts.append(dbutils.secrets.get(scope="neo4j", key="routing_host"))

for debug_host in debug_hosts:
    try:
        debug_ip = socket.gethostbyname(debug_host)
    except socket.gaierror as err:
        print(f"{debug_host} -> UNRESOLVED ({err})")
        continue
    kind = "private" if ipaddress.ip_address(debug_ip).is_private else "PUBLIC"
    print(f"{debug_host} -> {debug_ip} ({kind})")
