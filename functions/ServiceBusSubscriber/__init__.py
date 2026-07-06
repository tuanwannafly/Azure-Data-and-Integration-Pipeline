"""Azure Function: Service Bus Queue Trigger subscriber (US-08).

Consumes normalized market-tick messages from the `market-ticks` Service Bus
queue (published by the Finnhub WebSocket publisher) and upserts each tick into
the Azure SQL `live_ticks` table, keeping only the latest tick per symbol
(BR-02 idempotency — re-delivery updates the same row instead of inserting).

NOTE (2026-07-06): switched from pyodbc -> pymssql. Linux Consumption Plan
Function Apps do not ship the "ODBC Driver 18 for SQL Server" system package,
so pyodbc.connect() failed at runtime (worker crash / MaxDeliveryCountExceeded
dead-lettering, no exception ever reached App Insights). pymssql bundles its
own FreeTDS-based driver as a pip wheel, so no system-level driver install is
required on Consumption Plan.

The connection string for SQL is still read from AZURE_SQL_CONNECTION_STRING
(BR-01) for backward compatibility with the existing App Setting (ODBC-style
key=value string) — we parse out the fields pymssql needs.
Service Bus connection is wired via the Function App setting `ServiceBusConnection`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import azure.functions as func
import pymssql

logger = logging.getLogger(__name__)

# Upsert a tick: MERGE on symbol so duplicate deliveries do not create new rows.
UPSERT_SQL = """
MERGE INTO dbo.live_ticks AS target
USING (SELECT %s AS symbol, %s AS price, %s AS volume, %s AS trade_timestamp,
              %s AS received_at) AS source
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


def _parse_odbc_style_connection_string(conn: str) -> dict[str, str]:
    """Parse a semi-colon-delimited key=value connection string.

    Handles both the ODBC style used by pyodbc:
        Driver={ODBC Driver 18 for SQL Server};Server=tcp:HOST,1433;
        Database=DB;Uid=USER;Pwd=PASS;Encrypt=yes;TrustServerCertificate=no;
    and tolerates values containing '=' (e.g. base64 passwords) by splitting
    only on the first '='.
    """
    fields: dict[str, str] = {}
    for part in conn.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        fields[key.strip().lower()] = value.strip()
    return fields


def _resolve_sql_connection_params() -> dict[str, Any]:
    """Read AZURE_SQL_CONNECTION_STRING and extract pymssql connect() kwargs."""
    conn = os.getenv("AZURE_SQL_CONNECTION_STRING")
    if not conn or "REPLACE_ME" in conn:
        raise ValueError(
            "AZURE_SQL_CONNECTION_STRING env var is required to upsert ticks "
            "into live_ticks. Set it as a Function App setting."
        )

    fields = _parse_odbc_style_connection_string(conn)

    server_raw = fields.get("server", "")
    # Server value may look like "tcp:host.database.windows.net,1433"
    server_raw = re.sub(r"^tcp:", "", server_raw, flags=re.IGNORECASE)
    host, _, port = server_raw.partition(",")
    host = host.strip()
    port_int = int(port) if port.strip().isdigit() else 1433

    database = fields.get("database", "")
    user = fields.get("uid", "") or fields.get("user id", "") or fields.get("user", "")
    password = fields.get("pwd", "") or fields.get("password", "")

    missing = [
        name
        for name, val in (("Server", host), ("Database", database), ("Uid", user), ("Pwd", password))
        if not val
    ]
    if missing:
        raise ValueError(
            f"AZURE_SQL_CONNECTION_STRING is missing required field(s): {missing}"
        )

    return {
        "server": host,
        "port": port_int,
        "database": database,
        "user": user,
        "password": password,
    }


def _connect(retries: int = 3, delay: float = 2.0):
    """Open a pymssql connection with basic retry (BR-05)."""
    params = _resolve_sql_connection_params()
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return pymssql.connect(
                server=params["server"],
                port=params["port"],
                database=params["database"],
                user=params["user"],
                password=params["password"],
                login_timeout=30,
                timeout=30,
                as_dict=False,
            )
        except pymssql.Error as exc:
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


def upsert_tick(conn, tick: dict[str, Any]) -> None:
    """Execute the MERGE upsert for one tick (BR-02 idempotency)."""
    cur = conn.cursor()
    try:
        cur.execute(
            UPSERT_SQL,
            (
                tick["symbol"],
                tick["price"],
                tick["volume"],
                tick["trade_timestamp"],
                tick["received_at"],
            ),
        )
    finally:
        cur.close()
    conn.commit()


def main(msg: func.ServiceBusMessage) -> None:
    """Function entry point — invoked by the Service Bus trigger binding."""
    try:
        tick = parse_tick(msg.get_body().decode("utf-8"))
        logger.info("Upserting tick for symbol %s", tick["symbol"])
        conn = _connect()
        try:
            upsert_tick(conn, tick)
        finally:
            conn.close()
        logger.info("Tick upserted for symbol %s", tick["symbol"])
    except Exception:
        logger.exception("Failed to process market-tick message")
        raise
