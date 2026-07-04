"""Database helpers for the FastAPI API (US-09).

A thin pyodbc wrapper that reads AZURE_SQL_CONNECTION_STRING from env (BR-01).
Kept dependency-light: no ORM; each router issues one focused query and the
rows are serialized to dicts. This mirrors how a real "expose" layer reads the
 curated tables produced upstream.

All errors are caught at the router level (BR-06) — this module raises on DB
problems so the router can convert them into a clean HTTP response.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from typing import Any

import pyodbc

logger = logging.getLogger(__name__)


def _connection_string() -> str:
    cs = os.getenv("AZURE_SQL_CONNECTION_STRING", "")
    if not cs or "REPLACE_ME" in cs:
        raise RuntimeError(
            "AZURE_SQL_CONNECTION_STRING is not configured. Set it in App "
            "Settings / .env (BR-01)."
        )
    return cs


def query_all(sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    """Run a SELECT and return all rows as dicts (column name -> value)."""
    with pyodbc.connect(_connection_string(), timeout=15) as conn:
        conn.autocommit = True
        cursor = conn.cursor()
        cursor.execute(sql, *params)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]


def query_one(sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    """Run a SELECT and return the first row as a dict, or None if no rows."""
    with pyodbc.connect(_connection_string(), timeout=15) as conn:
        conn.autocommit = True
        cursor = conn.cursor()
        cursor.execute(sql, *params)
        columns = [col[0] for col in cursor.description]
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(zip(columns, row, strict=False))
