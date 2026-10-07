# Databricks notebook source
# MAGIC %md
# MAGIC # Private Link Smoke Test: Databricks classic cluster <-> Neo4j Aura
# MAGIC
# MAGIC End-to-end validation that data flows privately between a classic cluster in
# MAGIC this VNet-injected Azure Databricks workspace and the Aura instance named in the
# MAGIC `neo4j` secret scope, over an Azure private endpoint.
# MAGIC
# MAGIC **What this notebook does**
# MAGIC 1. Loads Neo4j credentials from the `neo4j` secret scope
# MAGIC 2. Resolves the Aura hostname and asserts a private IP
# MAGIC 3. Verifies TCP reachability on Bolt port 7687
# MAGIC 4. Resolves every routing host Aura advertises and asserts a private IP
# MAGIC 5. Opens a Bolt+TLS session and runs a sanity Cypher query
# MAGIC 6. Builds a 100-row Spark sample, writes it to Aura with batched UNWIND MERGE
# MAGIC 7. Reads the same rows back from Aura and asserts shape + counts
# MAGIC 8. Cleans up the test data (best-effort)
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - The private endpoint is approved in the Aura console
# MAGIC - The `databases.neo4j.io` private DNS zone is linked to the workspace VNet and
# MAGIC   holds an A record for the instance
# MAGIC - Restart the cluster after any DNS change, then attach this notebook to it
# MAGIC - Databricks secret scope `neo4j` populated with: `uri`, `username`, `password`
# MAGIC
# MAGIC **Widgets**
# MAGIC - `expected_pe_ip`: Optional. Pins the DNS check to the exact endpoint IP.
# MAGIC - `use_resolver`: Default `false`. Set `true` to map routing hosts back to the instance
# MAGIC   host. This hides missing routing-host records, so use it only to compare.

# COMMAND ----------

# MAGIC %pip install --quiet "neo4j==5.*" "tenacity==9.*"
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md ## 1. Load credentials and derive the target host

# COMMAND ----------

import ipaddress
import socket
import time
from urllib.parse import urlparse

dbutils.widgets.text("expected_pe_ip", "")
dbutils.widgets.dropdown("use_resolver", "false", ["false", "true"])
EXPECTED_PE_IP = dbutils.widgets.get("expected_pe_ip").strip()
USE_RESOLVER   = dbutils.widgets.get("use_resolver") == "true"

NEO4J_URI      = dbutils.secrets.get(scope="neo4j", key="uri")
NEO4J_USER     = dbutils.secrets.get(scope="neo4j", key="username")
NEO4J_PASSWORD = dbutils.secrets.get(scope="neo4j", key="password")
SECRET_KEYS    = {s.key for s in dbutils.secrets.list("neo4j")}
NEO4J_DATABASE = (
    dbutils.secrets.get(scope="neo4j", key="database")
    if "database" in SECRET_KEYS
    else "neo4j"
)

EXPECTED_HOST   = urlparse(NEO4J_URI).hostname
TEST_LABEL      = "DbxSmokeCustomer"
TEST_BATCH_TAG  = f"smoke-test-{int(time.time())}"
SAMPLE_ROWS     = 100
BATCH_SIZE      = 25

print("URI host : [REDACTED]")
print(f"User     : {NEO4J_USER}")
print(f"Run tag  : {TEST_BATCH_TAG}")

# COMMAND ----------

# MAGIC %md ## 2. DNS must resolve to a private IP
# MAGIC
# MAGIC If this fails, the private DNS zone is not linked to the workspace VNet, the instance
# MAGIC A record is missing, or the cluster needs a restart. See
# MAGIC `docs/operations/troubleshooting.md`.

# COMMAND ----------

ip = socket.gethostbyname(EXPECTED_HOST)
print(f"{EXPECTED_HOST} -> {ip}")
assert ipaddress.ip_address(ip).is_private, (
    f"DNS resolved to a public IP ({ip}). Private Link path is NOT active."
)
if EXPECTED_PE_IP:
    assert ip == EXPECTED_PE_IP, (
        f"DNS resolved to {ip}, but the private endpoint IP is {EXPECTED_PE_IP}. "
        "Update the A record to the current endpoint IP."
    )
print("OK: resolves to a private address.")

# COMMAND ----------

# MAGIC %md ## 3. TCP reachability on Bolt port 7687

# COMMAND ----------

with socket.create_connection((EXPECTED_HOST, 7687), timeout=10) as s:
    print(f"TCP connect succeeded: {s.getpeername()}")

# COMMAND ----------

# MAGIC %md ## 4. Routing hosts must resolve privately
# MAGIC
# MAGIC Aura VDC advertises routing hosts shaped like `p-<aura-instance-id>-<suffix>.<orch>.neo4j.io`.
# MAGIC Each needs its own record in the private DNS zone. This cell reads the routing table over
# MAGIC a direct `bolt+s` connection and resolves every advertised host.

# COMMAND ----------

import re

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, TransientError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

BOLT_PORT = urlparse(NEO4J_URI).port or 7687

with GraphDatabase.driver(
    f"bolt+s://{EXPECTED_HOST}:{BOLT_PORT}",
    auth=(NEO4J_USER, NEO4J_PASSWORD),
    connection_timeout=10,
) as direct, direct.session(database=NEO4J_DATABASE) as session:
    routing = session.run(
        "CALL dbms.routing.getRoutingTable({}, $db) YIELD servers", db=NEO4J_DATABASE
    ).single()

routing_hosts = sorted(
    {addr.rsplit(":", 1)[0] for server in routing["servers"] for addr in server["addresses"]}
)


def check_routing_host(name):
    try:
        infos = socket.getaddrinfo(name, BOLT_PORT, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return "FAIL", "does not resolve"
    found = sorted({info[4][0] for info in infos})
    public = [a for a in found if not ipaddress.ip_address(a).is_private]
    if public:
        return "FAIL", f"public {', '.join(public)}"
    if EXPECTED_PE_IP and found != [EXPECTED_PE_IP]:
        return "FAIL", f"{', '.join(found)} is not the endpoint IP"
    return "PASS", ", ".join(found)


results = {name: check_routing_host(name) for name in routing_hosts}
print(f"Routing hosts advertised: {len(routing_hosts)}")
failed = [name for name, (status, _) in results.items() if status == "FAIL"]
for name, (status, detail) in results.items():
    print(f"{status}  {name if status == 'FAIL' else '[REDACTED]'}  {detail}")

if failed and USE_RESOLVER:
    print(
        f"WARN: {len(failed)} routing host(s) fail DNS. use_resolver is true, so the driver "
        "maps them to the instance host. Clients without that resolver will fail."
    )
else:
    assert not failed, (
        f"{len(failed)} routing host(s) do not resolve to the private endpoint. Add a record "
        "for each with:\n"
        + "\n".join(f'  uv run scripts/private_link.py add-routing-host "{name}"' for name in failed)
        + "\nThen restart the cluster and run this notebook again."
    )
    print("OK: every advertised routing host resolves privately.")

# COMMAND ----------

# MAGIC %md ## 5. Bolt+TLS session and sanity query

# COMMAND ----------

# Aura VDC advertises routing hosts shaped like p-<aura-instance-id>-<suffix>.<orch>.neo4j.io;
# the instance id is the first label of the connection host.
dbid = EXPECTED_HOST.split(".")[0]
ROUTING_HOST_PATTERN = re.compile(rf"^p-{re.escape(dbid)}-.*\.neo4j\.io$")

def aura_private_resolver(address):
    # Maps routing hosts back to the instance host. Only installed when use_resolver is
    # true, because it hides routing-host DNS records that are missing.
    mapped_host = EXPECTED_HOST if ROUTING_HOST_PATTERN.match(address.host) else address.host
    if mapped_host != address.host:
        print(f"Resolver alias: {address.host}:{address.port} -> {mapped_host}:{address.port}")
    return [(mapped_host, address.port)]

RESOLVER = aura_private_resolver if USE_RESOLVER else None

driver = GraphDatabase.driver(
    NEO4J_URI,
    auth=(NEO4J_USER, NEO4J_PASSWORD),
    resolver=RESOLVER,
    connection_timeout=10,
    max_connection_lifetime=300,
)
driver.verify_connectivity()
print("Driver verify_connectivity() OK.")

with driver.session(database=NEO4J_DATABASE) as session:
    server_info = session.run("CALL dbms.components() YIELD name, versions").single()
    print(f"Server: {server_info['name']} {server_info['versions']}")

# COMMAND ----------

# MAGIC %md ## 6. Generate a 100-row Spark sample and write to Aura

# COMMAND ----------

from pyspark.sql import functions as F

sample_df = (
    spark.range(SAMPLE_ROWS)
        .select(
            F.concat(F.lit("cust-"), F.col("id").cast("string")).alias("id"),
            F.concat(F.lit("Customer "), F.col("id").cast("string")).alias("name"),
            F.when(F.col("id") % 3 == 0, F.lit("uksouth"))
             .when(F.col("id") % 3 == 1, F.lit("northeurope"))
             .otherwise(F.lit("westeurope")).alias("region"),
            F.current_timestamp().alias("signup_ts"),
            F.lit(TEST_BATCH_TAG).alias("run_tag"),
        )
)

source_count = sample_df.count()
print(f"Source rows: {source_count}")
assert source_count == SAMPLE_ROWS

# COMMAND ----------

MERGE_CYPHER = f"""
UNWIND $rows AS row
MERGE (c:{TEST_LABEL} {{id: row.id}})
SET c.name       = row.name,
    c.region     = row.region,
    c.signup_ts  = row.signup_ts,
    c.run_tag    = row.run_tag,
    c.updated_at = datetime()
"""

def write_partition(partition_iter):
    # Runs on the executors: open a driver per partition and build the retry here,
    # because the driver and tenacity state hold locks that cannot be pickled.
    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        retry=retry_if_exception_type((ServiceUnavailable, TransientError)),
        reraise=True,
    )
    def write_batch(conn, rows):
        with conn.session(database=NEO4J_DATABASE) as session:
            session.execute_write(lambda tx: tx.run(MERGE_CYPHER, rows=rows).consume())

    with GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
        resolver=RESOLVER,
    ) as conn:
        batch = []
        for row in partition_iter:
            d = row.asDict()
            # Cast timestamp to ISO string so Cypher receives a datetime-compatible value.
            d["signup_ts"] = d["signup_ts"].isoformat() if d["signup_ts"] is not None else None
            batch.append(d)
            if len(batch) >= BATCH_SIZE:
                write_batch(conn, batch)
                batch = []
        if batch:
            write_batch(conn, batch)

sample_df.foreachPartition(write_partition)
print("Write phase complete.")

# COMMAND ----------

# MAGIC %md ## 7. Read the rows back from Aura and assert

# COMMAND ----------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((ServiceUnavailable, TransientError)),
    reraise=True,
)
def read_back():
    cypher = f"""
    MATCH (c:{TEST_LABEL} {{run_tag: $run_tag}})
    RETURN c.id AS id, c.name AS name, c.region AS region
    ORDER BY c.id
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        return [r.data() for r in session.run(cypher, run_tag=TEST_BATCH_TAG)]

rows = read_back()
print(f"Read {len(rows)} rows back from Aura.")
assert len(rows) == SAMPLE_ROWS, (
    f"Round-trip count mismatch. Wrote {SAMPLE_ROWS}, read back {len(rows)}."
)

# Show a sample. display() works in Databricks; print as fallback.
try:
    display(spark.createDataFrame(rows))
except Exception:
    for r in rows[:5]:
        print(r)

# COMMAND ----------

# MAGIC %md ## 8. Cleanup test data
# MAGIC
# MAGIC Removes the nodes labeled with this run's tag so the smoke test is idempotent.

# COMMAND ----------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((ServiceUnavailable, TransientError)),
    reraise=True,
)
def cleanup():
    cypher = f"MATCH (c:{TEST_LABEL} {{run_tag: $run_tag}}) DETACH DELETE c"
    with driver.session(database=NEO4J_DATABASE) as session:
        session.execute_write(lambda tx: tx.run(cypher, run_tag=TEST_BATCH_TAG).consume())

cleanup()
print("Cleanup complete.")

# COMMAND ----------

driver.close()
print("Smoke test PASSED: Databricks classic cluster <-> Aura over Private Link is healthy.")
