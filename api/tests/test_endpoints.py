"""Unit tests for the FastAPI API endpoints (US-09).

Mock the DB layer (api.db) so tests run without an Azure SQL connection.
Covers: health, root, revenue/yearly (with/without cik, DB error), and
market/live (found, not found, DB error).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


def test_root_lists_endpoints():
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert "/revenue/yearly" in body["endpoints"]
    assert "/market/live" in body["endpoints"]


# --------------------------------------------------------------------------- #
# /revenue/yearly
# --------------------------------------------------------------------------- #
@patch("api.routers.revenue.query_all")
def test_revenue_yearly_without_cik_returns_rows(mock_query):
    mock_query.return_value = [
        {"cik": 320193, "company_name": "Apple Inc.", "fiscal_year": 2022,
         "revenue": 394328000000, "yoy_growth_pct": 7.79}
    ]
    resp = client.get("/revenue/yearly")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["rows"][0]["cik"] == 320193


@patch("api.routers.revenue.query_all")
def test_revenue_yearly_with_cik_filters(mock_query):
    mock_query.return_value = [
        {"cik": 320193, "fiscal_year": 2022, "revenue": 394328000000}
    ]
    resp = client.get("/revenue/yearly?cik=320193")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    # Verify the CIK was passed to the query
    assert mock_query.call_args[0][1] == (320193,)


@patch("api.routers.revenue.query_all", side_effect=RuntimeError("DB down"))
def test_revenue_yearly_db_error_returns_503(_mock):
    resp = client.get("/revenue/yearly")
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"].lower()


# --------------------------------------------------------------------------- #
# /market/live
# --------------------------------------------------------------------------- #
@patch("api.routers.market.query_one")
def test_market_live_with_symbol_found(mock_query):
    mock_query.return_value = {
        "symbol": "AAPL", "price": 195.12, "volume": 100.0,
        "trade_timestamp": 1700000000000,
    }
    resp = client.get("/market/live?symbol=AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["tick"]["price"] == 195.12


@patch("api.routers.market.query_one", return_value=None)
def test_market_live_symbol_not_found_returns_404(_mock):
    resp = client.get("/market/live?symbol=UNKNOWN")
    assert resp.status_code == 404
    assert "No tick yet" in resp.json()["detail"]


@patch("api.routers.market.query_all")
def test_market_live_without_symbol_returns_all(mock_query):
    mock_query.return_value = [
        {"symbol": "AAPL", "price": 195.0},
        {"symbol": "BINANCE:BTCUSDT", "price": 42000.0},
    ]
    resp = client.get("/market/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert body["ticks"][0]["symbol"] == "AAPL"


@patch("api.routers.market.query_all", side_effect=RuntimeError("DB down"))
def test_market_live_db_error_returns_503(_mock):
    resp = client.get("/market/live")
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"].lower()
