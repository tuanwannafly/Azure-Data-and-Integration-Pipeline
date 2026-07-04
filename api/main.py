"""FastAPI application exposing the SEC EDGAR + Finnhub data (US-09).

Endpoints:
  * GET /revenue/yearly?cik=...   — annual revenue + YoY growth (SEC EDGAR flow)
  * GET /market/live?symbol=...   — latest market tick (Finnhub streaming flow)
  * GET /health                   — liveness probe (no DB call)
  * GET /docs                     — Swagger UI for demos

The service connects to Azure SQL via AZURE_SQL_CONNECTION_STRING (BR-01).
Errors are normalized: no raw stack trace is returned to the caller (BR-06).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from api.routers import market, revenue

logger = logging.getLogger(__name__)

PROJECT_NAME = "Azure Data Integration API"
API_VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Log startup; connections are opened lazily per request to keep the
    app resilient when the SQL database is unreachable (e.g. local dev)."""
    cs = os.getenv("AZURE_SQL_CONNECTION_STRING", "")
    if not cs:
        logger.warning("AZURE_SQL_CONNECTION_STRING not set; DB endpoints will 503.")
    logger.info("Starting %s v%s", PROJECT_NAME, API_VERSION)
    yield
    logger.info("Shutting down %s", PROJECT_NAME)


app = FastAPI(
    title=PROJECT_NAME,
    version=API_VERSION,
    description=(
        "Exposes data from two pipelines:\n"
        "- SEC EDGAR (batch): annual revenue via /revenue/yearly\n"
        "- Finnhub (streaming): live ticks via /market/live"
    ),
    lifespan=lifespan,
)

app.include_router(revenue.router, prefix="/revenue", tags=["revenue"])
app.include_router(market.router, prefix="/market", tags=["market"])


@app.get("/health", tags=["meta"])
def health() -> dict[str, Any]:
    """Liveness probe — does not touch the database."""
    return {"status": "ok", "service": PROJECT_NAME, "version": API_VERSION}


@app.get("/", tags=["meta"])
def root() -> dict[str, Any]:
    """Redirect-like landing pointing at the docs."""
    return {
        "service": PROJECT_NAME,
        "docs": "/docs",
        "endpoints": ["/revenue/yearly", "/market/live", "/health"],
    }
