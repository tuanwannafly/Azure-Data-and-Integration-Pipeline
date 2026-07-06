# Databricks notebook source
# MAGIC %md
# MAGIC # transform_revenue.py — SEC EDGAR staging → company_revenue_yearly
# MAGIC
# MAGIC Reads `dbo.staging` (raw facts copied by ADF), dedupes to one row per
# MAGIC `(cik, fiscal_year)` (defensive — the adapter already dedupes upstream but
# MAGIC re-runs / schema drift can still land more than one candidate row per
# MAGIC year), computes `prev_revenue` + `yoy_growth_pct` via a window function,
# MAGIC and idempotently MERGEs the result into `dbo.company_revenue_yearly`
# MAGIC (BR-02: safe to re-run any number of times without duplicating rows).
# MAGIC
# MAGIC Wired into ADF as a Databricks Notebook Activity after `PL_CopyRawToStaging`,
# MAGIC or run manually for a demo (see `docs/demo-instructions.md`, Part A Step 4).

# COMMAND ----------

# MAGIC %md
# MAGIC ## Widgets — connection parameters (BR-01: no secrets hardcoded)
# MAGIC
# MAGIC `jdbc_password` should be backed by a Databricks Secret Scope linked to the
# MAGIC project's Key Vault (`kv-dataint-a3x9b`) rather than typed in plain text:
# MAGIC
# MAGIC ```bash
# MAGIC databricks secrets create-scope --scope kv-dataint-a3x9b \
# MAGIC     --scope-backend-type AZURE_KEYVAULT \
# MAGIC     --resource-id <key_vault_resource_id> \
# MAGIC     --dns-name https://kv-dataint-a3x9b.vault.azure.net/
# MAGIC ```
# MAGIC
# MAGIC Then replace the `dbutils.widgets.get("jdbc_password")` line below with:
# MAGIC `dbutils.secrets.get(scope="kv-dataint-a3x9b", key="sql-admin-password")`

# COMMAND ----------

dbutils.widgets.text("jdbc_hostname", "sql-dataint3dd244-dev.database.windows.net", "SQL Server hostname")
dbutils.widgets.text("jdbc_database", "sqldb-dataint3dd244", "Database name")
dbutils.widgets.text("jdbc_username", "", "SQL admin username")
dbutils.widgets.text("jdbc_password", "", "SQL admin password")

jdbc_hostname = dbutils.widgets.get("jdbc_hostname")
jdbc_database = dbutils.widgets.get("jdbc_database")
jdbc_username = dbutils.widgets.get("jdbc_username")
jdbc_password = dbutils.widgets.get("jdbc_password")

assert jdbc_username, "jdbc_username widget is empty — set the SQL admin login before running."
assert jdbc_password, "jdbc_password widget is empty — set the SQL admin password before running."

jdbc_port = 1433
jdbc_url = (
    f"jdbc:sqlserver://{jdbc_hostname}:{jdbc_port};"
    f"database={jdbc_database};encrypt=true;trustServerCertificate=false;"
    f"loginTimeout=30;"
)
connection_properties = {
    "user": jdbc_username,
    "password": jdbc_password,
    "driver": "com.microsoft.sqlserver.jdbc.SQLServerDriver",
}

STAGING_TABLE = "dbo.staging"
TARGET_TABLE = "dbo.company_revenue_yearly"
STAGE_TABLE = "dbo.company_revenue_yearly_stage"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 — Read `dbo.staging`

# COMMAND ----------

from pyspark.sql import functions as F
from pyspark.sql.window import Window
from pyspark.sql.types import DecimalType, LongType, IntegerType

staging_df = spark.read.jdbc(url=jdbc_url, table=STAGING_TABLE, properties=connection_properties)

print(f"Read {staging_df.count()} rows from {STAGING_TABLE}")
display(staging_df.limit(10))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 — Dedupe to one row per (cik, fiscal_year)
# MAGIC
# MAGIC Keep annual (`FY`) records only, then keep the row with the most recent
# MAGIC `filed_date` per `(cik, fiscal_year)` — this handles the case where a 10-K/A
# MAGIC amendment or a re-run of the adapter produced more than one candidate row
# MAGIC for the same company/year (the amendment should win, mirroring the same
# MAGIC rule already applied in `ingest/sec_edgar_adapter.py::_dedupe_latest_filing`).
# MAGIC `ingested_at` is used as a tiebreaker for identical `filed_date` values.

# COMMAND ----------

annual_df = staging_df.filter(F.col("fiscal_period") == "FY")

dedupe_window = Window.partitionBy("cik", "fiscal_year").orderBy(
    F.col("filed_date").desc_nulls_last(),
    F.col("ingested_at").desc_nulls_last(),
)

deduped_df = (
    annual_df
    .withColumn("_rn", F.row_number().over(dedupe_window))
    .filter(F.col("_rn") == 1)
    .drop("_rn")
)

print(f"Deduped {annual_df.count()} annual rows -> {deduped_df.count()} rows (one per cik/fiscal_year)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 — Compute prev_revenue + yoy_growth_pct
# MAGIC
# MAGIC Window partitioned by `cik`, ordered by `fiscal_year`, using `lag()` to
# MAGIC pull the previous year's revenue. The first fiscal year on record for a
# MAGIC company has no prior year, so `prev_revenue` / `yoy_growth_pct` are NULL —
# MAGIC matches the nullable columns in `infra/sql/schema.sql`.

# COMMAND ----------

yoy_window = Window.partitionBy("cik").orderBy("fiscal_year")

transformed_df = (
    deduped_df
    .withColumn("revenue", F.col("value").cast(DecimalType(38, 4)))
    .withColumn("prev_revenue", F.lag("revenue").over(yoy_window))
    .withColumn(
        "yoy_growth_pct",
        F.when(
            (F.col("prev_revenue").isNotNull()) & (F.col("prev_revenue") != 0),
            F.round(
                (F.col("revenue") - F.col("prev_revenue")) / F.col("prev_revenue") * 100,
                4,
            ).cast(DecimalType(10, 4)),
        ).otherwise(F.lit(None).cast(DecimalType(10, 4))),
    )
    .select(
        F.col("cik").cast(LongType()),
        F.col("company_name"),
        F.col("fiscal_year").cast(IntegerType()),
        F.col("revenue"),
        F.col("unit"),
        F.col("prev_revenue"),
        F.col("yoy_growth_pct"),
    )
)

print(f"Transformed {transformed_df.count()} rows ready to merge into {TARGET_TABLE}")
display(transformed_df.orderBy("cik", "fiscal_year"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4 — Write to a staging table, then MERGE (idempotent upsert, BR-02)
# MAGIC
# MAGIC Spark's JDBC writer has no native MERGE mode, so the standard pattern is:
# MAGIC 1. Overwrite a throwaway staging table with the transformed DataFrame.
# MAGIC 2. Open a raw JDBC connection (via the JVM gateway) and run a real
# MAGIC    `MERGE` statement from the staging table into the target table.
# MAGIC 3. Drop the staging table.
# MAGIC
# MAGIC Re-running this notebook any number of times produces the same end state
# MAGIC in `dbo.company_revenue_yearly` — no duplicate rows, values simply refresh.

# COMMAND ----------

(
    transformed_df.write
    .jdbc(url=jdbc_url, table=STAGE_TABLE, mode="overwrite", properties=connection_properties)
)
print(f"Wrote {transformed_df.count()} rows to staging table {STAGE_TABLE}")

# COMMAND ----------

merge_sql = f"""
MERGE INTO {TARGET_TABLE} AS target
USING {STAGE_TABLE} AS source
ON target.cik = source.cik AND target.fiscal_year = source.fiscal_year
WHEN MATCHED THEN
    UPDATE SET
        target.company_name   = source.company_name,
        target.revenue        = source.revenue,
        target.unit           = source.unit,
        target.prev_revenue   = source.prev_revenue,
        target.yoy_growth_pct = source.yoy_growth_pct,
        target.transformed_at = SYSDATETIME()
WHEN NOT MATCHED THEN
    INSERT (cik, company_name, fiscal_year, revenue, unit, prev_revenue, yoy_growth_pct, transformed_at)
    VALUES (source.cik, source.company_name, source.fiscal_year, source.revenue,
            source.unit, source.prev_revenue, source.yoy_growth_pct, SYSDATETIME());
"""

drop_stage_sql = f"IF OBJECT_ID(N'{STAGE_TABLE}', N'U') IS NOT NULL DROP TABLE {STAGE_TABLE};"

jvm = spark._sc._gateway.jvm
raw_conn = jvm.java.sql.DriverManager.getConnection(
    f"jdbc:sqlserver://{jdbc_hostname}:{jdbc_port};database={jdbc_database};"
    f"user={jdbc_username};password={jdbc_password};encrypt=true;"
    f"trustServerCertificate=false;loginTimeout=30;"
)
try:
    stmt = raw_conn.createStatement()
    try:
        rows_affected = stmt.executeUpdate(merge_sql)
        print(f"MERGE complete. Rows affected: {rows_affected}")
        stmt.executeUpdate(drop_stage_sql)
        print(f"Dropped staging table {STAGE_TABLE}")
    finally:
        stmt.close()
finally:
    raw_conn.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 5 — Verify

# COMMAND ----------

result_df = spark.read.jdbc(url=jdbc_url, table=TARGET_TABLE, properties=connection_properties)
display(result_df.orderBy("cik", "fiscal_year"))

# COMMAND ----------

# MAGIC %md
# MAGIC Sanity-check `yoy_growth_pct` against a public income statement (e.g. Apple's
# MAGIC 10-K) as noted in `docs/demo-instructions.md` Part A Step 4.