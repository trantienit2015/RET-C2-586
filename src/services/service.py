"""AgentCore Platform v1.0 - RET-C2-586 domain services.

Deterministic lead-time comparison + risk classification rule table
(Tool layer, no LLM) + LLM-optional alert narrative synthesis
(deterministic fallback when no LLM is configured). S-3 output gate is
non-suppressible: raw contract terms/pricing/margin data must never
appear in the alert report.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Protocol

_HIGH_RISK_CATEGORY_MULTIPLIER = 1.5
_RISK_THRESHOLD_HIGH_DAYS = 14
_RISK_THRESHOLD_MEDIUM_DAYS = 5

_HIGH_IMPACT_CATEGORIES = ("frozen", "seasonal", "cold-chain")

# Field-aware redaction of raw contract terms / pricing / margin.
# Narrow keyword regexes miss ordinary variants such as
# `price: $50`, `unit price: USD 50`, and `supplier margin of 20%`. These
# patterns normalise on the field keyword and accept an optional currency
# code/symbol, arbitrary connective words ("of"), and separators.
_CURRENCY = r"(?:[¥$€£]|USD|JPY|EUR|GBP|円)"
_CONTRACT_TERM_PATTERNS = [
    # any "... price ..." (unit price, list price, bare price) followed by an
    # amount, with an optional currency code/symbol before the digits.
    re.compile(
        rf"\b(?:unit\s+|list\s+|wholesale\s+)?price\b[:\s]*(?:of\s+)?{_CURRENCY}?\s*\d[\d,]*(?:\.\d+)?", re.IGNORECASE
    ),
    # a currency-prefixed amount on its own (e.g. "USD 50", "$50").
    re.compile(rf"{_CURRENCY}\s*\d[\d,]*(?:\.\d+)?", re.IGNORECASE),
    # "margin" (optionally "supplier margin", "gross margin of") + percent/number.
    re.compile(r"\b(?:supplier\s+|gross\s+|net\s+)?margin\b[:\s]*(?:of\s+)?\d+(?:\.\d+)?\s*%?", re.IGNORECASE),
    # explicit contract-term lines.
    re.compile(r"contract term[:\s]*.+", re.IGNORECASE),
]


class SupplierDBClient(Protocol):
    """Surface the injected supplier data source must expose."""

    def query(self, query_scope: dict[str, Any]) -> list[dict[str, Any]]: ...


def query_supplier_db(
    query_scope: dict[str, Any],
    db_records: list[dict[str, Any]] | None = None,
    client: "SupplierDBClient | None" = None,
) -> list[dict[str, Any]]:
    """Deterministic read-only query against the supplier/PO DB (Tool layer, no LLM).

    Resolution order:
    1. If a live `client` is supplied (built from the bound secret provider),
       fetch records via `client.query(query_scope)`. A connector failure
       propagates as `SupplierDBRequestError` so the caller can surface an
       explicit ERROR rather than a false-negative empty success.
    2. Otherwise fall back to caller-injected `db_records` (tests / stub path).

    A successful query returning zero rows is a legitimate empty result and is
    NOT the same as an unavailable data source (see pre_process error handling).
    """
    if client is not None:
        records = client.query(query_scope)
    else:
        records = db_records or []

    supplier_ids = query_scope.get("supplier_ids") or []
    category = query_scope.get("category")

    results = records
    if supplier_ids:
        results = [r for r in results if r.get("supplier_id") in supplier_ids]
    if category:
        results = [r for r in results if r.get("category") == category]
    return list(results)


def compute_delay_days(actual_date: str, contracted_date: str) -> int:
    """Deterministic date-delta computation (Tool layer, no LLM)."""
    actual = date.fromisoformat(actual_date)
    contracted = date.fromisoformat(contracted_date)
    return (actual - contracted).days


def classify_risk_tier(delay_days: int, category: str) -> str:
    """Deterministic rule-table risk classification (Tool layer, no LLM).

    (delay_days x category_type) -> risk_tier. High-impact categories
    (frozen/seasonal/cold-chain) apply a multiplier since delays there
    carry outsized supply-continuity risk. `category` may be `None` for a
    record that has the key with a null value (distinct from a missing key,
    which the caller already defaults to "") - normalise defensively so a
    malformed record cannot crash risk classification for the whole batch.
    """
    multiplier = _HIGH_RISK_CATEGORY_MULTIPLIER if (category or "").lower() in _HIGH_IMPACT_CATEGORIES else 1.0
    weighted_delay = delay_days * multiplier

    if weighted_delay >= _RISK_THRESHOLD_HIGH_DAYS:
        return "H"
    if weighted_delay >= _RISK_THRESHOLD_MEDIUM_DAYS:
        return "M"
    return "L"


def recommend_action(risk_tier: str) -> str:
    """Deterministic recommended-action mapping."""
    return {
        "H": "Escalate to supplier account manager; identify backup supplier",
        "M": "Monitor closely; confirm revised delivery commitment",
        "L": "No action required",
    }.get(risk_tier, "Escalate to supplier account manager; identify backup supplier")


def strip_contract_terms(text: str) -> tuple[str, bool]:
    """Non-suppressible re-check: strip raw contract terms/pricing/margin data.

    Returns (cleaned_text, was_blocked).
    """
    cleaned = text
    blocked = False
    for pattern in _CONTRACT_TERM_PATTERNS:
        if pattern.search(cleaned):
            blocked = True
            cleaned = pattern.sub("[REDACTED_CONTRACT_TERM]", cleaned)
    return cleaned, blocked


class AlertReportGenerationError(RuntimeError):
    """Raised when a configured LLM fails or returns an empty response.

    The deterministic narrative fallback is only valid when NO LLM is
    configured at all (self._llm is None — deterministic-core mode). When an
    LLM *is* configured but the call fails or returns empty content, the
    caller must surface an explicit ERROR rather than silently substituting
    the deterministic fallback for a real (but unavailable) LLM response
    (recipe 3m half-fix trap — a caller could otherwise never distinguish
    "LLM answered" from "LLM is down").
    """


def _extract_text(raw: Any) -> str:
    """Normalise an LLM response (canonical dict {"content": str} or bare string)."""
    if isinstance(raw, dict):
        content = raw.get("content", "")
        return content if isinstance(content, str) else ""
    if isinstance(raw, str):
        return raw
    return ""


def format_alert_report(at_risk_skus: list[dict[str, Any]], llm: Any | None = None) -> str:
    """Synthesize the alert report narrative.

    LLM-essential in a real deployment (contextual category-level risk
    narrative across many suppliers). The canonical BaseLLM.complete() -> dict
    response is normalised (not discarded when it is a dict). The
    deterministic narrative is used ONLY when no LLM is configured
    (`llm is None`) — a configured-but-failing LLM raises
    `AlertReportGenerationError` instead of silently falling back, so the
    caller can surface `AgentStatus.ERROR.value` (see recipe 3m).
    """
    if llm is not None and hasattr(llm, "complete"):
        messages = [
            {"role": "system", "content": "You are a retail supply-risk analyst."},
            {
                "role": "user",
                "content": (f"Write a contextual supply-risk alert narrative for these at-risk SKUs: {at_risk_skus}"),
            },
        ]
        try:
            text = _extract_text(llm.complete([{"role": "user", "content": messages}]))
        except (ConnectionError, TimeoutError, ValueError, RuntimeError) as exc:
            raise AlertReportGenerationError(f"LLM provider call failed: {exc}") from exc
        if not text.strip():
            raise AlertReportGenerationError("LLM returned an empty response")
        cleaned, _ = strip_contract_terms(text.strip())
        return cleaned

    if not at_risk_skus:
        return "No at-risk SKUs found for the given query scope."

    high_count = sum(1 for s in at_risk_skus if s.get("risk_tier") == "H")
    lines = [f"{len(at_risk_skus)} at-risk SKU(s) found, {high_count} high-risk."]
    for sku in at_risk_skus:
        lines.append(
            f"- {sku.get('sku_id')} ({sku.get('supplier')}, {sku.get('category')}): "
            f"{sku.get('delay_days')} days delayed, risk={sku.get('risk_tier')}, "
            f"action: {sku.get('recommended_action')}"
        )
    body = "\n".join(lines)
    cleaned, _ = strip_contract_terms(body)
    return cleaned
