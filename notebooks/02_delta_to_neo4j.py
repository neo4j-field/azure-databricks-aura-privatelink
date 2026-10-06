# Databricks notebook source
# MAGIC %md
# MAGIC # Delta → Neo4j Round Trip
# MAGIC
# MAGIC Production-pattern example:
# MAGIC 1. Read from a Delta table
# MAGIC 2. Batch UNWIND MERGE into Neo4j (idempotent)
# MAGIC 3. Query Neo4j and persist results back to Delta
# MAGIC
# MAGIC **Prerequisites:** run `01_validate_connectivity.py` first to confirm the private path works.

# COMMAND ----------

# MAGIC %pip install --quiet neo4j==5.* tenacity==9.*
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md ## Configuration

# COMMAND ----------

import re
from urllib.parse import urlparse

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, TransientError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from pyspark.sql import functions as F

NEO4J_URI      = dbutils.secrets.get(scope="neo4j", key="uri")
NEO4J_USER     = dbutils.secrets.get(scope="neo4j", key="username")
NEO4J_PASSWORD = dbutils.secrets.get(scope="neo4j", key="password")
NEO4J_DATABASE = "neo4j"

CATALOG        = "pldemo"
SOURCE_TABLE   = f"{CATALOG}.demo.customers"        # change to your Delta table
TARGET_TABLE   = f"{CATALOG}.demo.customer_metrics" # round-trip output
BATCH_SIZE     = 500

# COMMAND ----------

# MAGIC %md ## 0. (Optional) Create synthetic source table
# MAGIC
# MAGIC Skip this if `SOURCE_TABLE` already exists. The cell only creates the table when it is
# MAGIC missing, so it never overwrites real data.

# COMMAND ----------

if not spark.catalog.tableExists(SOURCE_TABLE):
    schema = SOURCE_TABLE.split(".")[1]
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{schema}")

    (spark.range(1000)
        .select(
            (F.col("id") + 1).alias("customer_id"),
            F.concat(F.lit("Customer "), (F.col("id") + 1).cast("string")).alias("name"),
            F.when(F.col("id") % 3 == 0, F.lit("uksouth"))
             .when(F.col("id") % 3 == 1, F.lit("northeurope"))
             .otherwise(F.lit("westeurope")).alias("region"),
            (F.current_timestamp() - F.expr("make_interval(0, 0, 0, CAST(id % 365 AS INT))"))
                .alias("signup_ts"),
        )
        .write.saveAsTable(SOURCE_TABLE))
    print(f"Created {SOURCE_TABLE}")
else:
    print(f"{SOURCE_TABLE} already exists, leaving it untouched")

# COMMAND ----------

# MAGIC %md ## 1. Read source Delta table

# COMMAND ----------

df = (spark.table(SOURCE_TABLE)
        .select(
            F.col("customer_id").cast("string").alias("id"),
            F.col("name"),
            F.col("region"),
            F.col("signup_ts"),
        )
        .where("id IS NOT NULL"))

row_count = df.count()
print(f"Source rows: {row_count}")

# COMMAND ----------

# MAGIC %md ## 2. Batch idempotent write to Neo4j
# MAGIC
# MAGIC Uses `UNWIND` + `MERGE` on a stable key. Safe to re-run.

# COMMAND ----------

def make_resolver(uri):
    # Aura VDC advertises p-<dbid>-*.neo4j.io routing hosts that do not resolve on
    # Databricks serverless. Map them to the private Aura host that NCC resolves.
    host = urlparse(uri).hostname
    pattern = re.compile(rf"^p-{re.escape(host.split('.')[0])}-.*\.neo4j\.io$")

    def resolver(address):
        mapped_host = host if pattern.match(address.host) else address.host
        return [(mapped_host, address.port)]

    return resolver

driver = GraphDatabase.driver(
    NEO4J_URI,
    auth=(NEO4J_USER, NEO4J_PASSWORD),
    resolver=make_resolver(NEO4J_URI),
)

MERGE_CYPHER = """
UNWIND $rows AS row
MERGE (c:Customer {id: row.id})
SET c.name      = row.name,
    c.region    = row.region,
    c.signup_ts = row.signup_ts,
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
        resolver=make_resolver(NEO4J_URI),
    ) as conn:
        batch = []
        for row in partition_iter:
            batch.append(row.asDict())
            if len(batch) >= BATCH_SIZE:
                write_batch(conn, batch)
                batch = []
        if batch:
            write_batch(conn, batch)

df.foreachPartition(write_partition)
print("Write phase complete.")

# COMMAND ----------

# MAGIC %md ## 3. Read aggregated results back from Neo4j

# COMMAND ----------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((ServiceUnavailable, TransientError)),
    reraise=True,
)
def fetch_region_metrics():
    cypher = """
    MATCH (c:Customer)
    RETURN c.region AS region, count(c) AS customers
    ORDER BY customers DESC
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        return [r.data() for r in session.run(cypher)]

metrics = fetch_region_metrics()
print(f"Regions returned: {len(metrics)}")

# COMMAND ----------

# MAGIC %md ## 4. Persist metrics to Delta

# COMMAND ----------

result_df = spark.createDataFrame(metrics)

(result_df.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(TARGET_TABLE))

display(spark.table(TARGET_TABLE))

# COMMAND ----------

driver.close()
print("Round trip complete.")
