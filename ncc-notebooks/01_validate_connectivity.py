# Databricks notebook source
# MAGIC %md
# MAGIC # Neo4j Aura PrivateLink Connectivity Validation
# MAGIC
# MAGIC End-to-end check that Azure Databricks reaches Neo4j Aura over Azure Private Link.
# MAGIC It runs on serverless compute for the NCC path, or on a classic cluster in a
# MAGIC VNet-injected workspace for the Private Link path.
# MAGIC
# MAGIC **Prerequisites, NCC path (serverless)**
# MAGIC - The NCC is attached to this workspace and its private endpoint rule reads `ESTABLISHED`.
# MAGIC   See docs/setup-ncc-manual.md or docs/setup-ncc-terraform.md.
# MAGIC
# MAGIC **Prerequisites, Private Link path (classic cluster)**
# MAGIC - The private endpoint is approved, and the `databases.neo4j.io` private DNS zone is
# MAGIC   linked to the workspace VNet. See docs/setup-private-link-manual.md or
# MAGIC   docs/setup-private-link-terraform.md.
# MAGIC
# MAGIC **Both paths**
# MAGIC - The Databricks secret scope `neo4j` holds the keys `uri`, `username`, and `password`.
# MAGIC   An optional `database` key overrides the default database `neo4j`.
# MAGIC
# MAGIC The driver in step 4 maps Aura routing hosts (`p-<aura-instance-id>-*.neo4j.io`) to the
# MAGIC instance host, so this notebook passes without routing-host DNS entries. Clients that
# MAGIC do not use such a resolver still need them.

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

import socket
from urllib.parse import urlparse

host = urlparse(NEO4J_URI).hostname
ip = socket.gethostbyname(host)
print(f"{host} -> {ip}")

is_private = (
    ip.startswith("10.") or
    ip.startswith("192.168.") or
    (ip.startswith("172.") and 16 <= int(ip.split(".")[1]) <= 31)
)

assert is_private, (
    f"DNS resolved to a public IP ({ip}). Private Link is NOT in use. "
    "NCC path: check that the private endpoint rule includes domain_names and is "
    "ESTABLISHED. Private Link path: check that the private DNS zone is linked to "
    "the workspace VNet and holds an A record for the instance."
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

# MAGIC %md ## 5. Verify driver routing info

# COMMAND ----------

driver.verify_connectivity()
print("Driver verify_connectivity() OK.")

# COMMAND ----------

driver.close()
print("Validation complete. Private Link path is working end-to-end.")
