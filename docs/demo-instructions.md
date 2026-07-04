# Demo Instructions

End-to-end demo script for the Azure Data Integration pipeline. Covers both the
**SEC EDGAR batch flow** and the **Finnhub streaming flow**.

## Prerequisites

- Azure resources deployed (see `README.md` → Deploy to Azure).
- SQL schema applied (`infra/sql/schema.sql`).
- `.env` populated with real secrets.
- Finnhub API key valid, at least one US market symbol configured.

---

## Part A — SEC EDGAR batch flow (P0 MVP)

### Step 1: Run the adapter (ingest)

```bash
python -m ingest.sec_edgar_adapter
```

- Fetches company facts for watched CIKs (Apple, Microsoft, Tesla by default).
- Normalizes nested XBRL → flat JSON, handles schema drift via fallback tags.
- Writes `data/raw_export_sec_<YYYYMMDD>_run01.json`.

**Verify:** open the JSON file, confirm `record_count > 0` and `tag_used` varies
across companies (proves the fallback map works).

### Step 2: Upload to Blob Storage

```bash
python -m ingest.blob_ingest
```

- Uploads the latest export to container `raw-sec` under `sec/sec_export_*.json`.

**Verify:** Azure Portal → Storage Account → Containers → `raw-sec` → see the blob.

### Step 3: Trigger the ADF pipeline

- Azure Portal → Data Factory → "Trigger Now" on the `PL_CopyRawToStaging` pipeline.
- Or via CLI: `az datafactory pipeline create-run ...`

**Verify:** ADF Monitor → pipeline run Succeeded. Then query SQL:

```sql
SELECT TOP 20 * FROM dbo.staging ORDER BY ingested_at DESC;
```

Records with different `tag_used` values prove schema-drift tolerance.

### Step 4: Databricks transform (P1)

Run the notebook `databricks/notebooks/transform_revenue.py` (wired into ADF as a
Databricks Notebook Activity, or run manually for the demo).

**Verify:**

```sql
SELECT cik, company_name, fiscal_year, revenue, yoy_growth_pct
FROM dbo.company_revenue_yearly
ORDER BY cik, fiscal_year;
```

Sanity-check YoY growth against a public income statement (e.g. Apple 10-K).

### Step 5: Query via FastAPI

```bash
curl "http://localhost:8000/revenue/yearly?cik=320193"
```

**Verify:** JSON response with revenue rows and `yoy_growth_pct`.

---

## Part B — Finnhub streaming flow (P1)

### Step 1: Start the publisher

Deployed as a Container App (always-on). For a live demo, run locally during US
market hours (Finnhub free tier serves real-time US stock trades):

```bash
python -m streaming.finnhub_ws_publisher
```

**Verify:** logs show "Subscribed to AAPL" and ticks being published to Service Bus.

### Step 2: Confirm Service Bus receiving messages

- Azure Portal → Service Bus Namespace → Queues → `market-ticks` → check
  "Active messages" or "Messages in" counters increasing.

### Step 3: Confirm Function upserting to `live_ticks`

The Service Bus subscriber Function auto-triggers on each message.

**Verify:**

```sql
SELECT * FROM dbo.live_ticks ORDER BY updated_at DESC;
```

Only one row per symbol (upsert/idempotency, BR-02).

### Step 4: Query live ticks via API — **the highlight**

Call `/market/live` twice, a few seconds apart, to show the price changing:

```bash
# First call
curl "http://localhost:8000/market/live?symbol=AAPL"
# {"symbol":"AAPL","tick":{"price":195.12,...}}

# Wait 5 seconds, call again
curl "http://localhost:8000/market/live?symbol=AAPL"
# {"symbol":"AAPL","tick":{"price":195.34,...}}  ← price moved
```

This visibly demonstrates the streaming pipeline is real-time.

---

## Part C — APIM gateway (P1)

```bash
# Without key → 401
curl "https://apim-dataintegration-dev.azure-api.net/revenue/yearly"

# With key → 200
curl -H "Ocp-Apim-Subscription-Key: <your-key>" \
     "https://apim-dataintegration-dev.azure-api.net/market/live?symbol=AAPL"
```

**Rate limit test:** call `/market/live` > 30 times in 60 seconds → 429 Too Many Requests.

---

## Cleanup (IMPORTANT — avoid charges)

After the demo, tear down cost-generating resources:

```bash
# 1. Stop the Finnhub publisher Container App (runs WebSocket 24/7!)
az containerapp stop -n ca-dataintegration-ws-dev -g rg-dataintegration-dev

# 2. Terminate Databricks cluster (if auto-termination wasn't set)
#    Workspace → Clusters → Terminate

# 3. (Optional) Delete the whole resource group to stop ALL charges
az group delete --name rg-dataintegration-dev --yes --no-wait
```

> The Container App running the WebSocket publisher is the #1 source of
> forgotten charges — always stop it after demoing.
