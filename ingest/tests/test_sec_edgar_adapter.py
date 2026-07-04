"""Unit tests for sec_edgar_adapter.py (US-01).

Covers: User-Agent enforcement, nested-fact flattening, schema drift via the
fallback map, and dedupe (10-K/A amend keeps the latest filed_date).
All tests use local fixture JSON — no network calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ingest import sec_edgar_adapter as sea
from ingest.sec_edgar_adapter import (
    SecEdgarError,
    fetch_company_facts,
    normalize_company_facts,
    run_full_ingest,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# User-Agent enforcement (BR-07)
# --------------------------------------------------------------------------- #
def test_resolve_user_agent_missing_raises(monkeypatch):
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    with pytest.raises(SecEdgarError, match="SEC_EDGAR_USER_AGENT"):
        sea._resolve_user_agent()


def test_resolve_user_agent_placeholder_raises(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Project REPLACE_ME email")
    with pytest.raises(SecEdgarError):
        sea._resolve_user_agent()


def test_headers_contain_user_agent(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Demo Project demo@example.com")
    headers = sea._headers()
    assert headers["User-Agent"] == "Demo Project demo@example.com"


# --------------------------------------------------------------------------- #
# Normalization / flattening
# --------------------------------------------------------------------------- #
def test_normalize_flattens_annual_revenue_records():
    facts = _load("sec_companyfacts_sample.json")
    records = normalize_company_facts(facts, cik=320193, company_name="Apple Inc.")
    # Only FY forms are kept; the Q2 10-Q entry is dropped.
    assert len(records) == 2
    assert {r["fiscal_year"] for r in records} == {2021, 2022}
    assert all(r["fiscal_period"] == "FY" for r in records)
    assert all(r["unit"] == "USD" for r in records)
    assert all(r["tag_used"] == "Revenues" for r in records)


def test_normalize_drops_non_annual_forms():
    facts = _load("sec_companyfacts_sample.json")
    records = normalize_company_facts(facts, cik=320193, company_name="Apple Inc.")
    forms = {r["form_type"] for r in records}
    assert "10-Q" not in forms


# --------------------------------------------------------------------------- #
# Schema drift: fallback tag selection (US-01 core)
# --------------------------------------------------------------------------- #
def test_normalize_uses_fallback_tag_when_revenues_absent():
    facts = _load("sec_companyfacts_fallback.json")
    records = normalize_company_facts(facts, cik=9999999, company_name="Fallsback Co.")
    assert len(records) == 1
    # The company lacks "Revenues" but has the ASC 606 tag, which comes first
    # in the fallback chain.
    assert records[0]["tag_used"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert records[0]["value"] == 1_200_000_000.0


def test_normalize_returns_empty_when_no_revenue_tag():
    facts = _load("sec_companyfacts_missing.json")
    records = normalize_company_facts(facts, cik=5555555, company_name="No Revenue Tag Co.")
    assert records == []


# --------------------------------------------------------------------------- #
# Idempotency prep: dedupe on (cik, fiscal_year) keeping latest filed_date
# --------------------------------------------------------------------------- #
def test_normalize_keeps_latest_filing_for_amend():
    facts = _load("sec_companyfacts_amend.json")
    records = normalize_company_facts(facts, cik=320193, company_name="Apple Inc.")
    # Same (cik, fy=2022) filed twice; the 10-K/A amend (later filed_date) wins.
    assert len(records) == 1
    assert records[0]["form_type"] == "10-K/A"
    assert records[0]["value"] == 394_330_000_000.0
    assert records[0]["filed_date"] == "2022-11-15"


def test_dedupe_is_deterministic_on_apple_fixture():
    facts = _load("apple_companyfacts.json")
    records = normalize_company_facts(facts, cik=320193, company_name="Apple Inc.")
    assert len(records) == 2
    years = [r["fiscal_year"] for r in records]
    assert years == sorted(years)


# --------------------------------------------------------------------------- #
# fetch_company_facts: retry / backoff on 403 and 429 (BR-05)
# --------------------------------------------------------------------------- #
def _mock_response(status: int, payload: dict | None = None):
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status.side_effect = (
        RuntimeError("raised") if status >= 400 and status not in (403, 429) else None
    )
    resp.json.return_value = payload or {}
    return resp


@patch("ingest.sec_edgar_adapter.time.sleep", return_value=None)
def test_fetch_retries_on_403_then_succeeds(_sleep, monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Demo demo@example.com")
    session = MagicMock()
    session.get.side_effect = [_mock_response(403), _mock_response(200, {"cik": 1})]
    data = fetch_company_facts(320193, session=session, backoff_seconds=0)
    assert data == {"cik": 1}
    assert session.get.call_count == 2


@patch("ingest.sec_edgar_adapter.time.sleep", return_value=None)
def test_fetch_raises_after_max_retries(_sleep, monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Demo demo@example.com")
    session = MagicMock()
    session.get.return_value = _mock_response(403)
    with pytest.raises(SecEdgarError):
        fetch_company_facts(320193, session=session, max_retries=2, backoff_seconds=0)


# --------------------------------------------------------------------------- #
# run_full_ingest: end-to-end with mocked HTTP, writes export file
# --------------------------------------------------------------------------- #
@patch("ingest.sec_edgar_adapter.time.sleep", return_value=None)
def test_run_full_ingest_writes_export(_sleep, tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Demo demo@example.com")
    apple = _load("sec_companyfacts_sample.json")

    session = MagicMock()
    session.get.return_value = _mock_response(200, apple)

    out = run_full_ingest(
        watched_ciks=[{"cik": 320193, "company_name": "Apple Inc."}],
        output_dir=str(tmp_path),
        session=session,
        sleep_between=0,
    )
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["record_count"] == 2
    assert {r["fiscal_year"] for r in payload["records"]} == {2021, 2022}


@patch("ingest.sec_edgar_adapter.time.sleep", return_value=None)
def test_run_full_ingest_skips_company_without_revenue_tag(_sleep, tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Demo demo@example.com")
    missing = _load("sec_companyfacts_missing.json")

    session = MagicMock()
    session.get.return_value = _mock_response(200, missing)

    out = run_full_ingest(
        watched_ciks=[{"cik": 5555555, "company_name": "No Revenue Tag Co."}],
        output_dir=str(tmp_path),
        session=session,
        sleep_between=0,
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["record_count"] == 0
