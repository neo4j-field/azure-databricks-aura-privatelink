# Databricks notebook source
# MAGIC %md
# MAGIC # PrivateLink Smoke Test: dbxuk-svrless-drose <-> Aura b7253d3b
# MAGIC
# MAGIC End-to-end validation that data flows privately between the
# MAGIC `dbxuk-svrless-drose` workspace (Azure Databricks Serverless, UK South)
# MAGIC and the Aura instance `b7253d3b.databases.neo4j.io` over Azure PrivateLink.
# MAGIC
# MAGIC **What this notebook does**
# MAGIC 1. Loads Neo4j credentials from the `neo4j` secret scope
# MAGIC 2. Resolves the Aura hostname and asserts a private IP
# MAGIC 3. Verifies TCP reachability on Bolt port 7687
# MAGIC 4. Opens a Bolt+TLS session and runs a sanity Cypher query
# MAGIC 5. Builds a 100-row Spark sample, writes it to Aura with batched UNWIND MERGE
# MAGIC 6. Reads the same rows back from Aura and asserts shape + counts
# MAGIC 7. Cleans up the test data (best-effort)
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - Run the Terraform under `infra/terraform/` so the NCC + PE rule exist
# MAGIC - Approve the incoming PE request in the Aura console
# MAGIC - Wait until the rule reads `ESTABLISHED` in the Databricks NCC view
# MAGIC - Restart any running serverless compute, then attach this notebook to
# MAGIC   a serverless cluster in the `dbxuk-svrless-drose` workspace
# MAGIC - Databricks secret scope `neo4j` populated with: `uri`, `username`, `password`

# COMMAND ----------

# MAGIC %pip install --quiet "neo4j==5.*" "tenacity==9.*"
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md ## 1. Load credentials and pin expected targets

# COMMAND ----------

import ipaddress
import socket
import time
from urllib.parse import urlparse

NEO4J_URI      = dbutils.secrets.get(scope="neo4j", key="uri")
NEO4J_USER     = dbutils.secrets.get(scope="neo4j", key="username")
NEO4J_PASSWORD = dbutils.secrets.get(scope="neo4j", key="password")
NEO4J_DATABASE = "neo4j"

EXPECTED_HOST   = "b7253d3b.databases.neo4j.io"
TEST_LABEL      = "DbxSmokeCustomer"
TEST_BATCH_TAG  = f"dbxuk-svrless-drose-{int(time.time())}"
SAMPLE_ROWS     = 100
BATCH_SIZE      = 25

resolved_host = urlparse(NEO4J_URI).hostname
assert resolved_host == EXPECTED_HOST, (
    f"Secret scope URI host ({resolved_host}) does not match the expected Aura instance "
    f"({EXPECTED_HOST}). Refusing to run the smoke test against the wrong target."
)
print(f"URI host : {resolved_host}")
print(f"User     : {NEO4J_USER}")
print(f"Run tag  : {TEST_BATCH_TAG}")

# COMMAND ----------

# MAGIC %md ## 2. DNS must resolve to a private IP
# MAGIC
# MAGIC If this fails, the NCC private endpoint rule is missing the `domain_names`
# MAGIC field or the rule is not yet `ESTABLISHED`. See `docs/troubleshooting.md`.

# COMMAND ----------

ip = socket.gethostbyname(EXPECTED_HOST)
print(f"{EXPECTED_HOST} -> {ip}")
assert ipaddress.ip_address(ip).is_private, (
    f"DNS resolved to a public IP ({ip}). Private Link path is NOT active."
)
print("OK: resolves to a private address.")

# COMMAND ----------

# MAGIC %md ## 3. TCP reachability on Bolt port 7687

# COMMAND ----------

with socket.create_connection((EXPECTED_HOST, 7687), timeout=10) as s:
    print(f"TCP connect succeeded: {s.getpeername()}")

# COMMAND ----------

# MAGIC %md ## 4. Bolt+TLS session and sanity query

# COMMAND ----------

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, TransientError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

ROUTING_HOST_ALIASES = {
    # Aura VDC can return p-*.neo4j.io addresses in the routing table. If those
    # names do not resolve in Databricks serverless, map them to the private
    # Aura hostname that NCC already resolves.
    "p-b7253d3b-944d-0005.production-orch-0477.neo4j.io": EXPECTED_HOST,
}

def aura_private_resolver(address):
    mapped_host = ROUTING_HOST_ALIASES.get(address.host, address.host)
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
driver.verify_connectivity()
print("Driver verify_connectivity() OK.")

with driver.session(database=NEO4J_DATABASE) as session:
    server_info = session.run("CALL dbms.components() YIELD name, versions").single()
    print(f"Server: {server_info['name']} {server_info['versions']}")

# COMMAND ----------

# MAGIC %md ## 5. Generate a 100-row Spark sample and write to Aura

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

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    retry=retry_if_exception_type((ServiceUnavailable, TransientError)),
    reraise=True,
)
def write_batch(rows):
    with driver.session(database=NEO4J_DATABASE) as session:
        session.execute_write(lambda tx: tx.run(MERGE_CYPHER, rows=rows).consume())

def write_partition(partition_iter):
    batch = []
    for row in partition_iter:
        d = row.asDict()
        # Cast timestamp to ISO string so Cypher receives a datetime-compatible value.
        d["signup_ts"] = d["signup_ts"].isoformat() if d["signup_ts"] is not None else None
        batch.append(d)
        if len(batch) >= BATCH_SIZE:
            write_batch(batch)
            batch = []
    if batch:
        write_batch(batch)
    return iter([])

sample_df.foreachPartition(write_partition)
print("Write phase complete.")

# COMMAND ----------

# MAGIC %md ## 6. Read the rows back from Aura and assert

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

# MAGIC %md ## 7. Cleanup test data
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
print("Smoke test PASSED — dbxuk-svrless-drose <-> Aura b7253d3b over PrivateLink is healthy.")
