"""Revenue router — reads company_revenue_yearly (SEC EDGAR flow) (US-09)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.db import query_all

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/yearly", response_model=None)
def yearly_revenue(
    cik: int | None = Query(None, ge=0, description="SEC CIK (optional). Omit for all companies."),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    """Return annual revenue + YoY growth per company/fiscal year.

    If `cik` is omitted, returns all watched companies (most recent first).
    """
    try:
        if cik is not None:
            sql = (
                "SELECT cik, company_name, fiscal_year, revenue, unit, "
                "prev_revenue, yoy_growth_pct, transformed_at "
                "FROM dbo.company_revenue_yearly WHERE cik = ? "
                "ORDER BY fiscal_year DESC"
            )
            rows = query_all(sql, (cik,))
        else:
            sql = (
                "SELECT TOP (?) cik, company_name, fiscal_year, revenue, unit, "
                "prev_revenue, yoy_growth_pct, transformed_at "
                "FROM dbo.company_revenue_yearly ORDER BY fiscal_year DESC"
            )
            rows = query_all(sql, (limit,))
    except Exception as exc:  # noqa: BLE001 — normalize DB errors (BR-06)
        logger.exception("Revenue query failed")
        raise HTTPException(status_code=503, detail="Revenue data unavailable") from exc

    return {"count": len(rows), "rows": rows}
