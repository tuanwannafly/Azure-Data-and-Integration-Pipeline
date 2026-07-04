"""Market router — reads live_ticks (Finnhub streaming flow) (US-09)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.db import query_all, query_one

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/live", response_model=None)
def live_ticks(
    symbol: str | None = Query(None, description="Ticker, e.g. AAPL. Omit for all symbols."),
) -> dict[str, Any]:
    """Return the latest market tick per symbol.

    If `symbol` is omitted, returns the full list of symbols currently tracked.
    If a requested symbol has no tick yet (publisher not running), returns 404
    with a clear message rather than a 500 (BR-06).
    """
    try:
        if symbol is not None:
            sql = (
                "SELECT symbol, price, volume, trade_timestamp, received_at, updated_at "
                "FROM dbo.live_ticks WHERE symbol = ?"
            )
            row = query_one(sql, (symbol,))
            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"No tick yet for symbol '{symbol}'. "
                    "The Finnhub publisher may not be running.",
                )
            return {"symbol": symbol, "tick": row}
        sql = (
            "SELECT symbol, price, volume, trade_timestamp, received_at, updated_at "
            "FROM dbo.live_ticks ORDER BY symbol"
        )
        rows = query_all(sql)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — normalize DB errors (BR-06)
        logger.exception("Market query failed")
        raise HTTPException(status_code=503, detail="Market data unavailable") from exc

    return {"count": len(rows), "ticks": rows}
