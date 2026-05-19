# Databricks notebook source
# MAGIC %md
# MAGIC # Serverless + PrivateLink Push / Pull Demo
# MAGIC
# MAGIC A small, focused notebook that pushes a Spark DataFrame into Neo4j Aura and
# MAGIC pulls aggregates back, with all traffic flowing privately over the
# MAGIC NCC-managed PrivateLink path.
# MAGIC
# MAGIC Run after the NCC stack under `infra/terraform/` is applied and the rule
# MAGIC reads `ESTABLISHED` in the Databricks account console.
# MAGIC
# MAGIC **Secret scope `neo4j`** must hold `uri`, `username`, `password`.

# COMMAND ----------

# MAGIC %pip install --quiet "neo4j==5.*"
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md ## Configure

# COMMAND ----------

from neo4j import GraphDatabase
from pyspark.sql import functions as F

NEO4J_URI      = dbutils.secrets.get(scope="neo4j", key="uri")
NEO4J_USER     = dbutils.secrets.get(scope="neo4j", key="username")
NEO4J_PASSWORD = dbutils.secrets.get(scope="neo4j", key="password")
NEO4J_DATABASE = "neo4j"

DEMO_LABEL = "DemoCustomer"
ROWS       = 20

# COMMAND ----------

# MAGIC %md ## 1. Build a 20-row Spark DataFrame

# COMMAND ----------

push_df = (
    spark.range(ROWS)
        .select(
            F.concat(F.lit("cust-"), F.col("id").cast("string")).alias("id"),
            F.concat(F.lit("Customer "), F.col("id").cast("string")).alias("name"),
            F.when(F.col("id") % 3 == 0, F.lit("uksouth"))
             .when(F.col("id") % 3 == 1, F.lit("northeurope"))
             .otherwise(F.lit("westeurope")).alias("region"),
        )
)
display(push_df)

# COMMAND ----------

# MAGIC %md ## 2. Push to Neo4j (single batched UNWIND MERGE)
# MAGIC
# MAGIC 20 rows comfortably fits in one transaction, so we collect on the driver
# MAGIC and write once. For larger sets, switch to `foreachPartition` with batches
# MAGIC of 1k-10k rows (see `notebooks/02_delta_to_neo4j.py`).

# COMMAND ----------

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

MERGE_CYPHER = f"""
UNWIND $rows AS row
MERGE (c:{DEMO_LABEL} {{id: row.id}})
SET c.name   = row.name,
    c.region = row.region
"""

rows = [r.asDict() for r in push_df.collect()]
with driver.session(database=NEO4J_DATABASE) as session:
    summary = session.execute_write(
        lambda tx: tx.run(MERGE_CYPHER, rows=rows).consume()
    )
print(f"Wrote {summary.counters.nodes_created} new nodes "
      f"and updated {summary.counters.properties_set} properties.")

# COMMAND ----------

# MAGIC %md ## 3. Pull aggregates back into Spark

# COMMAND ----------

PULL_CYPHER = f"""
MATCH (c:{DEMO_LABEL})
RETURN c.region AS region, count(c) AS customers
ORDER BY customers DESC
"""

with driver.session(database=NEO4J_DATABASE) as session:
    result = [r.data() for r in session.run(PULL_CYPHER)]

pull_df = spark.createDataFrame(result)
display(pull_df)

# COMMAND ----------

# MAGIC %md ## 4. Cleanup
# MAGIC
# MAGIC Comment out the next cell if you want the demo nodes to persist.

# COMMAND ----------

with driver.session(database=NEO4J_DATABASE) as session:
    session.execute_write(
        lambda tx: tx.run(f"MATCH (c:{DEMO_LABEL}) DETACH DELETE c").consume()
    )

driver.close()
print("Done.")
