"""Unit tests for functions/ServiceBusSubscriber/__init__.py (US-08).

The subscriber's job is to take a Service Bus message body and upsert it into
the dbo.live_ticks table. We mock pyodbc so no SQL connection is needed and
cover: parse_tick (string + dict body, missing/invalid fields) and upsert_tick
(MERGE statement shape + parameter binding).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from functions.ServiceBusSubscriber import parse_tick, upsert_tick


# --------------------------------------------------------------------------- #
# parse_tick: JSON-string body
# --------------------------------------------------------------------------- #
def test_parse_tick_from_json_string():
    body = json.dumps({
        "symbol": "AAPL",
        "price": 195.12,
        "volume": 100.0,
        "trade_timestamp": 1700000000000,
        "received_at": "2026-07-05T00:00:00+00:00",
    })
    tick = parse_tick(body)
    assert tick["symbol"] == "AAPL"
    assert tick["price"] == 195.12
    assert tick["volume"] == 100.0
    assert tick["trade_timestamp"] == 1700000000000
    assert tick["received_at"] == "2026-07-05T00:00:00+00:00"


def test_parse_tick_from_dict():
    body = {"symbol": "BINANCE:BTCUSDT", "price": 42000.5}
    tick = parse_tick(body)
    assert tick["symbol"] == "BINANCE:BTCUSDT"
    assert tick["price"] == 42000.5
    assert tick["volume"] is None


def test_parse_tick_missing_required_field_raises():
    with pytest.raises(ValueError, match="Missing required tick field"):
        parse_tick(json.dumps({"price": 1.0}))


def test_parse_tick_empty_symbol_raises():
    with pytest.raises(ValueError, match="symbol must be a non-empty string"):
        parse_tick(json.dumps({"symbol": "", "price": 1.0}))


def test_parse_tick_invalid_price_raises():
    with pytest.raises(ValueError, match="price must be numeric"):
        parse_tick(json.dumps({"symbol": "AAPL", "price": "not-a-number"}))


def test_parse_tick_unsupported_type_raises():
    with pytest.raises(ValueError, match="Unsupported message body type"):
        parse_tick(12345)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# upsert_tick: MERGE statement shape
# --------------------------------------------------------------------------- #
def test_upsert_tick_executes_merge_with_correct_params():
    conn = MagicMock()
    cursor = MagicMock()
    # upsert_tick calls conn.cursor() (not as a context manager); returning
    # the same MagicMock each time satisfies both the bare .cursor() and the
    # `with conn.cursor() as cur` form some drivers expose.
    conn.cursor.return_value = cursor

    tick = {
        "symbol": "AAPL",
        "price": 195.12,
        "volume": 100.0,
        "trade_timestamp": 1700000000000,
        "received_at": "2026-07-05T00:00:00+00:00",
    }
    upsert_tick(conn, tick)

    cursor.execute.assert_called_once()
    args, kwargs = cursor.execute.call_args
    sql = args[0]
    # pymssql binds parameters positionally as a single tuple; pyodbc would
    # pass them as separate args. Either way the tuple contents must match.
    params = args[1] if len(args) > 1 else kwargs.get("parameters")
    if isinstance(params, tuple) and len(params) == 1 and isinstance(params[0], tuple):
        params = params[0]
    assert "MERGE INTO dbo.live_ticks" in sql
    assert "ON (target.symbol = source.symbol)" in sql
    assert "WHEN MATCHED THEN" in sql
    assert "WHEN NOT MATCHED THEN" in sql
    assert list(params) == [
        "AAPL",
        195.12,
        100.0,
        1700000000000,
        "2026-07-05T00:00:00+00:00",
    ]
    conn.commit.assert_called_once()


def test_upsert_tick_raises_when_db_call_fails():
    """If the SQL driver raises, the exception propagates so the Functions
    host can apply its retry/dead-letter policy (BR-05)."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = RuntimeError("transient")
    conn.cursor.return_value = cursor

    tick = {"symbol": "AAPL", "price": 1.0, "volume": 1.0,
            "trade_timestamp": 1, "received_at": "x"}
    with pytest.raises(RuntimeError, match="transient"):
        upsert_tick(conn, tick)
