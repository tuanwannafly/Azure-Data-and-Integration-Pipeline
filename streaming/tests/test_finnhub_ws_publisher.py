"""Unit tests for finnhub_ws_publisher.py (US-07).

Mock the WebSocket so tests run without a live Finnhub connection.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from streaming.finnhub_ws_publisher import (
    build_ws_url,
    normalize_trade_message,
    parse_symbols,
)


def test_parse_symbols_splits_env():
    assert parse_symbols("AAPL,BINANCE:BTCUSDT") == ["AAPL", "BINANCE:BTCUSDT"]


def test_parse_symbols_default_when_empty():
    assert parse_symbols("") == ["AAPL"]


def test_build_ws_url_includes_token():
    url = build_ws_url("demo-key")
    assert url.startswith("wss://ws.finnhub.io")
    assert "token=demo-key" in url


def test_normalize_trade_message_extracts_fields():
    raw = {
        "data": [
            {"s": "AAPL", "p": 195.12, "v": 100.0, "t": 1700000000000}
        ],
        "type": "trade",
    }
    ticks = normalize_trade_message(json.dumps(raw))
    assert len(ticks) == 1
    tick = ticks[0]
    assert tick["symbol"] == "AAPL"
    assert tick["price"] == 195.12
    assert tick["volume"] == 100.0
    assert tick["trade_timestamp"] == 1700000000000
    assert "received_at" in tick


def test_normalize_trade_message_ignores_non_trade():
    raw = {"type": "ping"}
    assert normalize_trade_message(json.dumps(raw)) == []


def test_normalize_trade_message_ignores_empty():
    assert normalize_trade_message("") == []
    assert normalize_trade_message("not json") == []


@patch("streaming.finnhub_ws_publisher.ServiceBusClient")
def test_send_tick_to_service_bus_calls_sender(mock_sb_cls, monkeypatch):
    monkeypatch.setenv("SERVICE_BUS_CONNECTION_STRING", "Endpoint=sb://x;")
    from streaming.finnhub_ws_publisher import publish_tick

    mock_client = MagicMock()
    mock_sender = MagicMock()
    mock_sb_cls.from_connection_string.return_value = mock_client
    # publish_tick uses nested context managers: with Client() as c: with c.get_queue_sender() as s:
    mock_client.__enter__.return_value = mock_client
    mock_client.get_queue_sender.return_value.__enter__.return_value = mock_sender

    tick = {"symbol": "AAPL", "price": 1.0, "volume": 1.0, "trade_timestamp": 1, "received_at": "x"}
    publish_tick(tick, queue="market-ticks")

    mock_sender.send_messages.assert_called_once()


@patch("streaming.finnhub_ws_publisher.create_connection")
@patch("streaming.finnhub_ws_publisher.ServiceBusClient")
def test_run_publisher_connects_and_subscribes(mock_sb_cls, mock_ws, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "key")
    monkeypatch.setenv("FINNHUB_SYMBOLS", "AAPL")
    monkeypatch.setenv("SERVICE_BUS_CONNECTION_STRING", "Endpoint=sb://x;")

    from streaming.finnhub_ws_publisher import FinnhubPublisher

    publisher = FinnhubPublisher(symbols=["AAPL"])
    mock_sender = MagicMock()
    mock_conn = MagicMock()
    mock_ws.return_value = mock_conn
    # Simulate one message then stop via max_messages.
    trade = {"data": [{"s": "AAPL", "p": 1.0, "v": 1.0, "t": 1}], "type": "trade"}
    mock_conn.recv.side_effect = [json.dumps(trade), StopIteration()]

    publisher.run(max_messages=1, sender=mock_sender)

    mock_conn.send.assert_any_call(json.dumps({"type": "subscribe", "symbol": "AAPL"}))
    mock_sender.send_messages.assert_called_once()
