"""Configuration for the SEC EDGAR adapter.

CIK list and tag fallback map are data-driven (not hardcoded in code) so the
adapter stays flexible when adding/removing watched companies (US-01).
"""

from __future__ import annotations

import os

# Default watched companies: (CIK, display name). SEC CIKs are 10-digit zero
# padded in the URL path but stored as integers here.
WATCHED_CIKS: list[dict[str, object]] = [
    {"cik": 320193, "company_name": "Apple Inc."},
    {"cik": 789019, "company_name": "Microsoft Corporation"},
    {"cik": 1318605, "company_name": "Tesla, Inc."},
]

# Override via env var if provided: FINNHUB_SYMBOLS-style comma list
# CIKS=320193,789019
_env_ciks = os.getenv("CIKS")
if _env_ciks:
    WATCHED_CIKS = [
        {"cik": int(c.strip()), "company_name": f"CIK {c.strip()}"}
        for c in _env_ciks.split(",")
        if c.strip().isdigit()
    ]

# SEC EDGAR endpoints
SEC_FACTS_URL_TEMPLATE = (
    "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
)
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# SEC rate-limit policy: ~10 requests/second. Keep well under that.
SEC_REQUEST_DELAY_SECONDS = 0.15

# Default output directory for raw JSON export (git-ignored).
RAW_EXPORT_DIR = os.getenv("RAW_EXPORT_DIR", "data")


def get_watched_ciks() -> list[dict[str, object]]:
    """Resolve the watched-CIK list at runtime.

    Reading env vars on every call avoids mutating module state at import time
    (which is bad practice: importing the module from tests can change the
    observed list if tests patch the env). Returns either the CIKS env override
    (when set to a digit-only comma list) or the static WATCHED_CIKS default.
    """
    raw = os.getenv("CIKS")
    if raw:
        parsed: list[dict[str, object]] = []
        for token in (t.strip() for t in raw.split(",")):
            if token.isdigit():
                parsed.append({"cik": int(token), "company_name": f"CIK {token}"})
        if parsed:
            return parsed
    return list(WATCHED_CIKS)
