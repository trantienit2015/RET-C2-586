# RET-C2-586 - Adversarial tests for output contract-term redaction.
#
# Narrow keyword regexes miss ordinary pricing/margin variants; these assert
# the field-aware patterns block them.

import pytest

from src.services.service import strip_contract_terms

_BLOCKED_VARIANTS = [
    "price: $50",
    "unit price: USD 50",
    "supplier margin of 20%",
    "list price of ¥1,200",
    "gross margin 33.5%",
    "The unit price is $12.99 per case.",
    "Contract term: net-60 exclusive.",
]

_ALLOWED = [
    "3 at-risk SKU(s) found, 1 high-risk.",
    "- SKU-001 (SUP-A, frozen): 14 days delayed, risk=H, action: Escalate",
    "No at-risk SKUs found for the given query scope.",
]


@pytest.mark.parametrize("text", _BLOCKED_VARIANTS)
def test_contract_terms_are_blocked(text):
    _, blocked = strip_contract_terms(text)
    assert blocked, f"expected redaction to block: {text!r}"


@pytest.mark.parametrize("text", _ALLOWED)
def test_benign_alert_text_is_not_blocked(text):
    _, blocked = strip_contract_terms(text)
    assert not blocked, f"benign alert text must not be blocked: {text!r}"
