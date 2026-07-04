"""Azure Function: Service Bus Queue Trigger subscriber (US-08).

Consumes normalized market-tick messages from the `market-ticks` Service Bus
queue (published by the Finnhub WebSocket publisher) and upserts each tick into
the Azure SQL `live_ticks` table, keeping only the latest tick per symbol
(BR-02 idempotency — re-delivery updates the same row instead of inserting).

The connection string for SQL is read from `AZURE_SQL_CONNECTION_STRING` (BR-01).
Service Bus connection is wired via the Function App setting `ServiceBusConnection`.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import azure.functions as func
import pyodbc

logger = logging.getLogger(__name__)

# Upsert a tick: MERGE on symbol so duplicate deliveries do not create new rows.
UPSERT_SQL = """
MERGE INTO dbo.live_ticks AS target
USING (SELECT ? AS symbol, ? AS price, ? AS volume, ? AS trade_timestamp,
              ? AS received_at) AS source
ON (target.symbol = source.symbol)
WHEN MATCHED THEN
    UPDATE SET
        price           = source.price,
        volume          = source.volume,
        trade_timestamp = source.trade_timestamp,
        received_at     = source.received_at,
        updated_at      = SYSDATETIME()
WHEN NOT MATCHED THEN
    INSERT (symbol, price, volume, trade_timestamp, received_at, updated_at)
    VALUES (source.symbol, source.price, source.volume,
            source.trade_timestamp, source.received_at, SYSDATETIME());
"""

# Fields expected in the Service Bus message body.
REQUIRED_FIELDS = ("symbol", "price")


def _resolve_sql_connection_string() -> str:
    """Read the mandatory Azure SQL connection string from env (BR-01)."""
    conn = os.getenv("AZURE_SQL_CONNECTION_STRING")
    if not conn or "REPLACE_ME" in conn:
        raise ValueError(
            "AZURE_SQL_CONNECTION_STRING env var is required to upsert ticks "
            "into live_ticks. Set it as a Function App setting."
        )
    return conn


def _connect(retries: int = 3, delay: float = 2.0) -> pyodbc.Connection:
    """Open a pyodbc connection with basic retry (BR-05)."""
    conn_str = _resolve_sql_connection_string()
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return pyodbc.connect(conn_str, timeout=30)
        except pyodbc.Error as exc:
            last_exc = exc
            logger.warning(
                "SQL connect attempt %s/%s failed: %s", attempt, retries, exc
            )
            if attempt < retries:
                time.sleep(delay * attempt)
    raise RuntimeError(f"Could not connect to Azure SQL: {last_exc}")


def parse_tick(raw: Any) -> dict[str, Any]:
    """Parse and validate a Service Bus message into a normalized tick dict.

    Accepts either a JSON string or a dict. Validates required fields.
    Raises ValueError on missing/invalid fields so the Function host can
    apply its dead-letter / retry policy.
    """
    if isinstance(raw, dict):
        payload = raw
    elif isinstance(raw, str):
        payload = json.loads(raw)
    else:
        raise ValueError(f"Unsupported message body type: {type(raw).__name__}")

    missing = [f for f in REQUIRED_FIELDS if f not in payload]
    if missing:
        raise ValueError(f"Missing required tick field(s): {missing}")

    symbol = str(payload["symbol"])
    if not symbol:
        raise ValueError("symbol must be a non-empty string")

    try:
        price = float(payload["price"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"price must be numeric: {payload['price']!r}") from exc

    volume = payload.get("volume")
    volume_f = float(volume) if volume is not None else None

    trade_ts = payload.get("trade_timestamp")
    received = payload.get("received_at")

    return {
        "symbol": symbol,
        "price": price,
        "volume": volume_f,
        "trade_timestamp": trade_ts,
        "received_at": received,
    }


def upsert_tick(conn: pyodbc.Connection, tick: dict[str, Any]) -> None:
    """Execute the MERGE upsert for one tick (BR-02 idempotency)."""
    with conn.cursor() as cur:
        cur.execute(
            UPSERT_SQL,
            tick["symbol"],
            tick["price"],
            tick["volume"],
            tick["trade_timestamp"],
            tick["received_at"],
        )
    conn.commit()


def main(msg: func.ServiceBusMessage) -> None:
    """Function entry point — invoked by the Service Bus trigger binding."""
    try:
        tick = parse_tick(msg.get_body().decode("utf-8"))
        logger.info("Upserting tick for symbol %s", tick["symbol"])
        with _connect() as conn:
            upsert_tick(conn, tick)
        logger.info("Tick upserted for symbol %s", tick["symbol"])
    except Exception:
        logger.exception("Failed to process market-tick message")
        raise
