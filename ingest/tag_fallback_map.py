"""XBRL tag fallback map (schema-drift handling for SEC EDGAR).

Different filers report the same economic concept under different US-GAAP tags,
and the same company may switch tags across years. This module defines ordered
fallback chains so the adapter can pick the first available tag for a given
concept (US-01 / BR-02 idempotency preparation).

Usage:
    from ingest.tag_fallback_map import pick_revenue_tag

    chosen = pick_revenue_tag(available_tags_for_company)
"""

from __future__ import annotations

# Ordered fallback chains per concept. First tag that exists in the company's
# facts wins. Order roughly reflects modern GAAP practice (ASC 606 revenue)
# falling back to older / simpler tags.
CONCEPT_FALLBACK_CHAINS: dict[str, list[str]] = {
    "revenue": [
        # Modern ASC 606 "Revenue from Contract with Customer"
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        # Traditional "total revenues"
        "Revenues",
        "SalesRevenueNet",
        # Older / sector-specific alternatives
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
    ],
}


def pick_tag_for_concept(
    concept: str,
    available_tags: dict[str, object],
) -> str | None:
    """Return the first tag from the fallback chain present in `available_tags`.

    Args:
        concept: logical concept key, e.g. "revenue".
        available_tags: keys present under facts.us-gaap.<tag> for a company.
            Passing a dict makes membership checks O(1) and tolerant of the
            full nested structure being flattened to just tag names.

    Returns:
        The chosen tag name, or None if no tag in the chain is available
        (the adapter logs a warning and skips that company rather than crash).
    """
    chain = CONCEPT_FALLBACK_CHAINS.get(concept, [])
    if not available_tags:
        return None
    for tag in chain:
        if tag in available_tags:
            return tag
    return None


def pick_revenue_tag(available_tags: dict[str, object]) -> str | None:
    """Convenience wrapper for the revenue concept."""
    return pick_tag_for_concept("revenue", available_tags)
