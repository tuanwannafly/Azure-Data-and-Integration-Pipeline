# Azure Data & Integration Pipeline

> End-to-end data integration pipeline covering **ingest → orchestrate (ADF) → transform (Databricks) → event-driven (Service Bus / Function) → expose (FastAPI) → gateway (APIM)**, with CI/CD via GitHub Actions.
>
> Two real data sources (no mocks): **SEC EDGAR** (batch REST, financial facts) and **Finnhub** (real-time WebSocket trade ticks).

## Architecture

Two parallel flows converge into one FastAPI expose layer:

```
            ┌─────────────── SEC EDGAR (batch) ───────────────┐
            │                                                  │
 SEC EDGAR  │  adapter  →  Blob  →  ADF Copy  →  staging(SQL) │
 companyfacts│ (Py)      (raw)     Activity       (dbo)        │
            │                              ↓                  │
            │                  Databricks transform           │
            │                  (dedupe + YoY)                 │
            │                              ↓                  │
            │              company_revenue_yearly (SQL)       │
            └──────────────┬───────────────────────────────────┘
                           │
                           ▼
                     FastAPI  ──→  APIM gateway
                    /revenue        (subscription key
                    /market          + rate limit)
                           ▲
            ┌──────────────┴───────────────────────────────────┐
            │              live_ticks (SQL)                    │
            │                    ▲                             │
            │     Azure Function (Queue Trigger)               │
            │                    ▲                             │ (Finnhub streaming)
            │            Service Bus queue                     │
            │              "market-ticks"                      │
            │                    ▲                             │
            │       Finnhub WS publisher (Container App)       │
            │            wss://ws.finnhub.io                   │
            └──────────────────────────────────────────────────┘
```

A textual Mermaid version is in `docs/architecture-diagram.mmd`; the rendered
PNG (used in this README) is generated from it by `docs/render_diagram.py`.

## Repository layout

```
azure-data-integration/
├── ingest/          SEC EDGAR adapter + Blob upload (US-01, US-02)
├── adf/             Data Factory linked services, datasets, pipeline, trigger
├── databricks/      PySpark transform notebook (US-05, US-06)
├── streaming/       Finnhub WebSocket publisher (US-07)
├── functions/       Service Bus subscriber Azure Function (US-08)
├── api/             FastAPI expose layer (US-09, US-10)
├── infra/           Bicep IaC + SQL schema + APIM policy
├── .github/         CI + deploy workflows
└── docs/            Architecture diagram + demo instructions
```

## Quick start (local)

```bash
# 1. Clone and create a virtualenv
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# 2. Install dev + module dependencies
pip install -r requirements-dev.txt
pip install -r ingest/requirements.txt
pip install -r streaming/requirements.txt
pip install -r functions/requirements.txt
pip install -r api/requirements.txt

# 3. Copy env template and fill in real values (NEVER commit .env)
copy .env.example .env   # (Windows)
# Edit .env: set SEC_EDGAR_USER_AGENT, FINNHUB_API_KEY, connection strings

# 4. Lint + test
ruff check .
pytest -ra -q

# 5. Run the SEC EDGAR adapter locally (writes data/raw_export_sec_*.json)
python -m ingest.sec_edgar_adapter

# 6. Run the FastAPI locally
uvicorn api.main:app --reload --port 8000
# Open http://localhost:8000/docs for Swagger UI
```

## Deploy to Azure

### Prerequisites

- An Azure subscription with the Contributor role.
- [Azure CLI](https://learn.microsoft.com/cli/azure/) installed.
- A [Finnhub](https://finnhub.io/) free API key.
- A valid SEC EDGAR `User-Agent` (format: `"Project Name email@domain.com"`).

### 1. Deploy infrastructure (Bicep)

```bash
az group create --name rg-dataintegration-dev --location eastus

az deployment group create \
  --resource-group rg-dataintegration-dev \
  --template-file infra/main.bicep \
  --parameters projectName=dataintegration environment=dev \
  --parameters sqlAdminPassword='<your-strong-password>'
```

This provisions: Storage Account (`raw-sec`, `raw-finnhub`), Azure SQL Server + Database, Service Bus Namespace + `market-ticks` queue.

### 2. Apply SQL schema

```bash
sqlcmd -S sql-dataintegration-dev.database.windows.net \
  -d sqldb-dataintegration -U sqladmin -P '<password>' \
  -i infra/sql/schema.sql
```

Creates tables: `staging`, `company_revenue_yearly`, `live_ticks`, `watched_ciks`.

### 3. GitHub Secrets (for CI/CD)

Set these in **Settings → Secrets and variables → Actions**:

| Secret | Purpose |
|---|---|
| `AZURE_CREDENTIALS` | Service Principal JSON (`az ad sp create-for-rbac --role Contributor`) |
| `AZURE_SQL_ADMIN_PASSWORD` | SQL admin password for Bicep deploy |
| `SEC_EDGAR_USER_AGENT` | SEC EDGAR User-Agent header |
| `FINNHUB_API_KEY` | Finnhub WebSocket token |
| `FINNHUB_SYMBOLS` | Comma-separated symbols (e.g. `AAPL,BINANCE:BTCUSDT`) |
| `SERVICE_BUS_CONNECTION_STRING` | Service Bus namespace connection |
| `AZURE_STORAGE_CONNECTION_STRING` | Blob Storage connection |
| `AZURE_SQL_CONNECTION_STRING` | Azure SQL ODBC connection |

### 4. CI/CD

- **CI** (`.github/workflows/ci.yml`): runs `ruff check` + `pytest` on every PR to `develop`/`main`.
- **Deploy** (`.github/workflows/deploy.yml`): deploys Bicep + SQL schema + App Service on push to `main`.

## Business rules

| Rule | Description |
|---|---|
| BR-01 | No hardcoded secrets — use env vars / GitHub Secrets; `.env` is git-ignored. |
| BR-02 | Idempotency: SEC dedupes by `(cik, fiscal_year)` latest filed_date; Finnhub upserts by `symbol`. |
| BR-03 | Databricks cluster auto-terminates after 15-30 min idle. |
| BR-04 | Every Azure resource tagged `project=azure-data-integration`. |
| BR-05 | Retry + backoff on ADF Copy, Function, and Finnhub WS reconnect. |
| BR-06 | API never returns raw stack traces — errors are normalized. |
| BR-07 | SEC EDGAR requests always include a valid `User-Agent`, throttled ~10 req/s. |

## CI status

[![CI](https://github.com/<org>/azure-data-integration/actions/workflows/ci.yml/badge.svg)](https://github.com/<org>/azure-data-integration/actions/workflows/ci.yml)

## License

MIT — see `LICENSE` (this is a portfolio/learning project).