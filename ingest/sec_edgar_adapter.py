"""SEC EDGAR Company Facts adapter (batch).

Calls the SEC EDGAR `companyfacts` XBRL API, normalizes the deeply nested
US-GAAP facts into a flat JSON record per company/fiscal-period, and handles
schema drift across filers via the tag fallback map (US-01).

Key SEC EDGAR specifics (BR-07):
  * A valid `User-Agent` header is MANDATORY. Missing/invalid format -> HTTP 403.
  * Rate limit ~10 requests/second; we throttle between CIK requests.

Output record schema (stable):
    {
      "cik": int,
      "company_name": str,
      "tag_used": str,
      "fiscal_year": int,
      "fiscal_period": str,   # FY, Q1..Q4
      "form_type": str,        # 10-K, 10-Q, 10-K/A ...
      "end_date": "YYYY-MM-DD",
      "filed_date": "YYYY-MM-DD",
      "value": float,
      "unit": "USD"
    }

Idempotency (BR-02 prep): one company may re-file (10-K/A amend). We keep only
the record with the latest `filed_date` per (cik, fiscal_year).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ingest.config import (
    RAW_EXPORT_DIR,
    SEC_FACTS_URL_TEMPLATE,
    SEC_REQUEST_DELAY_SECONDS,
    WATCHED_CIKS,
)
from ingest.tag_fallback_map import pick_revenue_tag

logger = logging.getLogger(__name__)

# Concept is fixed to revenue for this pipeline, but the structure allows
# extending to other concepts (net income, etc.) trivially.
CONCEPT = "revenue"
TARGET_UNIT = "USD"
TARGET_FORMS_ANNUAL = {"10-K", "10-K/A"}


class SecEdgarError(RuntimeError):
    """Raised for non-retryable SEC EDGAR failures (e.g. invalid User-Agent)."""


def _resolve_user_agent() -> str:
    """Read the mandatory SEC User-Agent from env (BR-01/BR-07)."""
    ua = os.getenv("SEC_EDGAR_USER_AGENT")
    if not ua or "REPLACE_ME" in ua:
        raise SecEdgarError(
            "SEC_EDGAR_USER_AGENT env var is required and must follow the format "
            "'Project Name email@domain.com'. Without it SEC returns HTTP 403."
        )
    return ua


def _headers() -> dict[str, str]:
    return {
        "User-Agent": _resolve_user_agent(),
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov",
    }


def fetch_company_facts(
    cik: int,
    *,
    session: requests.Session | None = None,
    max_retries: int = 3,
    backoff_seconds: float = 2.0,
) -> dict[str, Any]:
    """Fetch raw company facts JSON for one CIK with retry/backoff.

    Retries on transient 403/429/network errors (BR-05). Raises SecEdgarError
    on persistent failure.
    """
    url = SEC_FACTS_URL_TEMPLATE.format(cik=cik)
    sess = session or requests
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            response = sess.get(url, headers=_headers(), timeout=30)
            if response.status_code in (403, 429):
                # Rate limited / blocked — back off and retry.
                logger.warning(
                    "SEC returned %s for CIK %s (attempt %s/%s); backing off %.1fs",
                    response.status_code, cik, attempt, max_retries, backoff_seconds,
                )
                time.sleep(backoff_seconds * attempt)
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            logger.warning(
                "Error fetching CIK %s (attempt %s/%s): %s",
                cik, attempt, max_retries, exc,
            )
            if attempt < max_retries:
                time.sleep(backoff_seconds * attempt)

    raise SecEdgarError(f"Failed to fetch company facts for CIK {cik}: {last_exc}")


def _iter_annual_revenue_records(
    facts: dict[str, Any], cik: int, company_name: str
) -> Iterator[dict[str, Any]]:
    """Yield flattened annual revenue records from raw company facts.

    Resolves schema drift with the fallback map: picks the first available
    revenue tag for the company, then reads annual (FY) units in USD.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        logger.warning("CIK %s (%s): no us-gaap facts present, skipping.", cik, company_name)
        return

    tag = pick_revenue_tag(us_gaap)
    if tag is None:
        logger.warning(
            "CIK %s (%s): no revenue tag found in fallback chain; skipping (schema drift).",
            cik, company_name,
        )
        return

    tag_node = us_gaap[tag]
    units = tag_node.get("units", {}).get(TARGET_UNIT, [])
    for entry in units:
        # entry keys: start, end, val, accn, fy, fp, form, filed, frame
        fp = entry.get("fp")
        form = entry.get("form", "")
        if fp != "FY" or form not in TARGET_FORMS_ANNUAL:
            continue
        try:
            yield {
                "cik": cik,
                "company_name": company_name,
                "tag_used": tag,
                "fiscal_year": int(entry["fy"]),
                "fiscal_period": fp,
                "form_type": form,
                "end_date": entry.get("end"),
                "filed_date": entry.get("filed"),
                "value": float(entry["val"]),
                "unit": TARGET_UNIT,
            }
        except (KeyError, ValueError, TypeError) as exc:
            logger.debug("CIK %s: skipping malformed entry %r (%s)", cik, entry, exc)


def _dedupe_latest_filing(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the latest-filed record per (cik, fiscal_year) (BR-02 prep).

    A company may file a 10-K/A amendment; we keep the newest filed_date.
    """
    latest: dict[tuple[int, int], dict[str, Any]] = {}
    for rec in records:
        key = (rec["cik"], rec["fiscal_year"])
        existing = latest.get(key)
        if existing is None or _filed(rec) > _filed(existing):
            latest[key] = rec
    # Sort for deterministic output.
    return sorted(latest.values(), key=lambda r: (r["cik"], r["fiscal_year"]))


def _filed(rec: dict[str, Any]) -> str:
    return rec.get("filed_date") or ""


def normalize_company_facts(
    facts: dict[str, Any], cik: int, company_name: str
) -> list[dict[str, Any]]:
    """Normalize raw company facts into flat, deduplicated annual records."""
    raw_records = list(_iter_annual_revenue_records(facts, cik, company_name))
    return _dedupe_latest_filing(raw_records)


def run_full_ingest(
    watched_ciks: list[dict[str, object]] | None = None,
    *,
    output_dir: str | None = None,
    session: requests.Session | None = None,
    sleep_between: float = SEC_REQUEST_DELAY_SECONDS,
) -> Path:
    """Fetch + normalize all watched CIKs and write one JSON export file.

    Returns the path to the written file (consumed by blob_ingest.py / US-02).
    """
    watched = watched_ciks if watched_ciks is not None else WATCHED_CIKS
    out_dir = Path(output_dir or RAW_EXPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_records: list[dict[str, Any]] = []
    own_session = session is None
    sess = session or requests.Session()

    try:
        for entry in watched:
            cik = int(entry["cik"])
            name = str(entry.get("company_name", f"CIK {cik}"))
            try:
                facts = fetch_company_facts(cik, session=sess)
            except SecEdgarError as exc:
                logger.error("Skipping CIK %s: %s", cik, exc)
                continue
            records = normalize_company_facts(facts, cik, name)
            logger.info("CIK %s (%s): %s annual records normalized.", cik, name, len(records))
            all_records.extend(records)
            if sleep_between > 0:
                time.sleep(sleep_between)
    finally:
        if own_session:
            sess.close()

    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    run_id = os.getenv("RUN_ID", "run01")
    out_path = out_dir / f"raw_export_sec_{run_ts}_{run_id}.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "source": "SEC EDGAR companyfacts",
                "concept": CONCEPT,
                "record_count": len(all_records),
                "records": all_records,
            },
            fh,
            indent=2,
            ensure_ascii=False,
        )
    logger.info("Wrote %s records to %s", len(all_records), out_path)
    return out_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    path = run_full_ingest()
    print(f"Export written to: {path}")
