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

### 3. GitHub Environment + Secrets setup (one-time, manual)

The pipeline uses **OIDC Workload Identity Federation** instead of a long-lived
service-principal secret, so GitHub never stores a credential capable of
authenticating as the SP outside an active workflow run.

**3a. Create the OIDC application**

```bash
# 1. After `az login`, run this once from any machine with Contributor rights
#    on the subscription. The script prints the values for the next step.
GITHUB_REPO="<org>/azure-data-integration" \
bash scripts/setup_azure_oidc.sh
```

**3b. Create the Azure Container Registry**

```bash
export AZURE_CLIENT_ID=<printed-by-setup_azure_oidc.sh>
bash scripts/setup_acr.sh
```

**3c. Configure GitHub**

In **Settings → Secrets and variables → Actions**, add the following
**Repository secrets**:

| Secret | Source |
|---|---|
| `AZURE_CLIENT_ID` | printed by `setup_azure_oidc.sh` |
| `AZURE_TENANT_ID` | printed by `setup_azure_oidc.sh` |
| `AZURE_SUBSCRIPTION_ID` | printed by `setup_azure_oidc.sh` |
| `AZURE_SQL_ADMIN_PASSWORD` | any password you choose (≥12 chars, complex) |
| `AZURE_SQL_CONNECTION_STRING_DEV`   | `sqlcmd`-built connection string for the **dev**   Azure SQL |
| `AZURE_SQL_CONNECTION_STRING_STAGING` | same, for **staging** |
| `AZURE_SQL_CONNECTION_STRING_PROD`  | same, for **prod**  |
| `AZURE_STORAGE_CONNECTION_STRING_DEV/STAGING/PROD` | per-env storage account conn string |
| `SERVICE_BUS_CONNECTION_STRING_DEV/STAGING/PROD` | per-env Service Bus namespace conn string |
| `FINNHUB_API_KEY`            | free tier key from https://finnhub.io |
| `FINNHUB_SYMBOLS`            | e.g. `AAPL,BINANCE:BTCUSDT` |
| `SEC_EDGAR_USER_AGENT`       | your project identifier: `"Project Name you@example.com"` |
| `CODECOV_TOKEN`              | *(optional)* upload coverage to codecov.io |
| `DEPLOY_NOTIFY_WEBHOOK`      | *(optional)* Slack/Teams incoming-webhook URL |

> **Why per-env connection strings?** Each environment is a separate Azure SQL
> server / Service Bus namespace. Keeping secrets scoped per environment means a
> leak in one environment can't reach the others.

In **Settings → Environments**, create three environments and (optionally)
require reviewers for `staging` and `prod`:

| Environment | Required reviewers | URL pattern |
|---|---|---|
| `dev`     | none               | `https://app-dataintegration-dev.azurewebsites.net` |
| `staging` | 1 team member      | `https://app-dataintegration-staging.azurewebsites.net` |
| `prod`    | 2 team members     | `https://app-dataintegration-prod.azurewebsites.net` |

In **Settings → Code security and analysis** enable Dependabot alerts and
secret scanning. The repository ships with `.github/dependabot.yml` and
`.gitleaks.toml` wired into CI.

In **Settings → Branches → Branch protection rules**, apply the policies in
`.github/branch-protection.md`. A `gh api` snippet is included there.

**3d. Promote code through environments**

```
        ┌──── feature/* ───► PR ───► develop ───► CD/dev (auto)
        │
main ───┼
        │
        └──── tag: staging-* ─► CD/staging (slot, canary)
              tag: v1.2.0    ─► CD/prod   (slot swap, manual approval)
```

- **`feature/* → develop`** — auto-deploy to **dev** on every push (concurrency group `cd-dev`).
- **`tag: staging-*`** — deploy to **staging** slot; can be smoke-tested manually before swap.
- **`tag: vX.Y.Z`** — deploy to **prod** slot, run extended smoke tests, then swap to prod. Requires 2 reviewers in the GitHub `prod` Environment.

### 4. CI workflows (run on every PR / push to develop / main)

| Workflow | Jobs | Trigger |
|---|---|---|
| `ci.yml` | `lint`, `test` (Python 3.10 + 3.11 on Linux/Windows, with coverage), `secret-scan` (gitleaks) | PR + push to develop / main |
| `cd-dev.yml` | `lint`, `infra`, `build-and-push` (multi-arch image), `deploy-api` / `deploy-function` / `deploy-containerapp`, `smoke-test` | push to `develop` |
| `cd-staging.yml` | same chain, but deploys to **staging** slot with extra smoke tests | push tag `staging-*` or manual |
| `cd-prod.yml` | same chain with **slot swap** + post-swap smoke test, **rollback issue** open on failure | push tag `vX.Y.Z` or manual |
| `cd-infra.yml` | shared `workflow_call` for provisioning Bicep + SQL schema, parameterized by environment | called by the three CD workflows |

Required status checks before `main` can be merged (set in branch protection):

- `CI / lint`
- `CI / test (Python 3.10, linux)`
- `CI / test (Python 3.11, linux)`
- `CI / secret-scan`
- `CD/dev / lint`

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
[![CD/dev](https://github.com/<org>/azure-data-integration/actions/workflows/cd-dev.yml/badge.svg)](https://github.com/<org>/azure-data-integration/actions/workflows/cd-dev.yml)
[![CD/staging](https://github.com/<org>/azure-data-integration/actions/workflows/cd-staging.yml/badge.svg)](https://github.com/<org>/azure-data-integration/actions/workflows/cd-staging.yml)
[![CD/prod](https://github.com/<org>/azure-data-integration/actions/workflows/cd-prod.yml/badge.svg)](https://github.com/<org>/azure-data-integration/actions/workflows/cd-prod.yml)
[![codecov](https://codecov.io/gh/<org>/azure-data-integration/graph/badge.svg)](https://codecov.io/gh/<org>/azure-data-integration)
[![Dependabot](https://img.shields.io/badge/Dependabot-enabled-blue.svg)](https://docs.github.com/en/code-security/dependabot)

Replace `<org>` with your GitHub org/user once you fork this repo.

## License

MIT — see `LICENSE` (this is a portfolio/learning project).