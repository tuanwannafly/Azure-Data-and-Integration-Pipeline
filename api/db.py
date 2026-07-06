"""Database helpers for the FastAPI API (US-09).

NOTE (2026-07-06): switched from pyodbc -> pymssql. Azure App Service Linux
(Python built-in image) does not ship the "ODBC Driver 18 for SQL Server"
system package (libodbc.so), so `import pyodbc` succeeds but the app can
crash-loop or 500 at connection time. pymssql bundles its own FreeTDS-based
driver as a pip wheel, so no system-level driver install is required —
matches the fix already applied to functions/ServiceBusSubscriber.

Reads AZURE_SQL_CONNECTION_STRING from env (BR-01), in the existing
ODBC-style key=value format, and parses out the fields pymssql needs.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Sequence
from typing import Any

import pymssql

logger = logging.getLogger(__name__)


def _parse_odbc_style_connection_string(conn: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in conn.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        fields[key.strip().lower()] = value.strip()
    return fields


def _connection_params() -> dict[str, Any]:
    cs = os.getenv("AZURE_SQL_CONNECTION_STRING", "")
    if not cs or "REPLACE_ME" in cs:
        raise RuntimeError(
            "AZURE_SQL_CONNECTION_STRING is not configured. Set it in App "
            "Settings / .env (BR-01)."
        )

    fields = _parse_odbc_style_connection_string(cs)

    server_raw = fields.get("server", "")
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
        raise RuntimeError(
            f"AZURE_SQL_CONNECTION_STRING is missing required field(s): {missing}"
        )

    return {
        "server": host,
        "port": port_int,
        "database": database,
        "user": user,
        "password": password,
    }


def _connect():
    params = _connection_params()
    return pymssql.connect(
        server=params["server"],
        port=params["port"],
        database=params["database"],
        user=params["user"],
        password=params["password"],
        login_timeout=15,
        timeout=15,
        as_dict=False,
    )


def _to_pymssql_placeholders(sql: str) -> str:
    """Translate pyodbc-style '?' placeholders to pymssql-style '%s'."""
    return sql.replace("?", "%s")


def query_all(sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(_to_pymssql_placeholders(sql), tuple(params))
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
    finally:
        conn.close()


def query_one(sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(_to_pymssql_placeholders(sql), tuple(params))
        columns = [col[0] for col in cursor.description]
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(zip(columns, row, strict=False))
    finally:
        conn.close()
