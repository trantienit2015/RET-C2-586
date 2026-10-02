# RET-C2-586 - Unit tests: S-4 domain audit events on every node execution path.
#
# Every node execution path must emit a meaningful, non-sensitive
# domain event, and tests must assert those events. Framework lifecycle events
# (node_start / node_complete / node_error) are emitted by BaseNode.__call__()
# and are deliberately NOT asserted here.

import src.nodes.lead_time_compare_risk_classify_node as main_mod
import src.nodes.post_process_node as post_mod
import src.nodes.pre_process_node as pre_mod
from framework.schemas.agent_status import AgentStatus

from src.nodes.lead_time_compare_risk_classify_node import LeadTimeCompareRiskClassifyNode
from src.nodes.post_process_node import OutputValidateNode
from src.nodes.pre_process_node import InputValidateSupplierDataQueryNode
from src.schemas.state import to_json

DB_RECORDS = [
    {
        "sku_id": "SKU-001",
        "supplier_id": "SUP-A",
        "category": "frozen",
        "actual_delivery_date": "2026-07-15",
        "contracted_delivery_date": "2026-07-01",
    },
]

# Values that must never leak into an audit payload.
SENSITIVE_MARKERS = ("SKU-001", "SUP-A", "frozen", "price", "margin", "$")


def _recorder(monkeypatch, module):
    """Patch emit_trace_event in the node module and record every call."""
    calls = []

    def fake_emit(event_type, payload, state=None):
        calls.append((event_type, payload))

    monkeypatch.setattr(module, "emit_trace_event", fake_emit)
    return calls


def _assert_non_sensitive(payload):
    blob = repr(payload)
    for marker in SENSITIVE_MARKERS:
        assert marker not in blob, f"audit payload leaked sensitive marker {marker!r}: {blob}"


class TestInputValidateSupplierDataQueryNodeAudit:
    def test_success_path_emits_domain_event(self, monkeypatch):
        calls = _recorder(monkeypatch, pre_mod)
        state = {"user_input": to_json({"supplier_ids": ["SUP-A"]}), "correlation_id": "corr-1"}
        r = InputValidateSupplierDataQueryNode(db_records=DB_RECORDS).execute(state)

        assert r["status"] == AgentStatus.SUCCESS.value
        assert [c[0] for c in calls] == ["supplier_query_received"]
        payload = calls[0][1]
        assert payload["correlation_id"] == "corr-1"
        assert payload["has_input"] is True
        _assert_non_sensitive(payload)

    def test_empty_input_error_path_emits_domain_event(self, monkeypatch):
        calls = _recorder(monkeypatch, pre_mod)
        r = InputValidateSupplierDataQueryNode().execute({"user_input": ""})

        assert r["status"] == AgentStatus.ERROR.value
        assert [c[0] for c in calls] == ["supplier_query_received"]
        assert calls[0][1]["has_input"] is False
        _assert_non_sensitive(calls[0][1])

    def test_unavailable_datasource_error_path_emits_domain_event(self, monkeypatch):
        calls = _recorder(monkeypatch, pre_mod)
        state = {"user_input": to_json({"supplier_ids": ["SUP-A"]})}
        r = InputValidateSupplierDataQueryNode().execute(state)

        assert r["status"] == AgentStatus.ERROR.value
        assert [c[0] for c in calls] == ["supplier_query_received"]
        assert calls[0][1]["live_client"] is False


class TestLeadTimeCompareRiskClassifyNodeAudit:
    def test_success_path_emits_domain_event(self, monkeypatch):
        calls = _recorder(monkeypatch, main_mod)
        state = {"supplier_records": to_json(DB_RECORDS), "correlation_id": "corr-2"}
        r = LeadTimeCompareRiskClassifyNode().execute(state)

        assert r["status"] == AgentStatus.SUCCESS.value
        assert [c[0] for c in calls] == ["lead_time_risk_classified"]
        payload = calls[0][1]
        assert payload["correlation_id"] == "corr-2"
        assert payload["records"] == 1
        assert payload["at_risk"] == 1
        _assert_non_sensitive(payload)

    def test_invalid_records_error_path_emits_domain_event(self, monkeypatch):
        calls = _recorder(monkeypatch, main_mod)
        r = LeadTimeCompareRiskClassifyNode().execute({"supplier_records": ""})

        assert r["status"] == AgentStatus.ERROR.value
        assert [c[0] for c in calls] == ["lead_time_risk_input_rejected"]
        payload = calls[0][1]
        assert payload["reason"] == "supplier_records_missing_or_invalid"
        _assert_non_sensitive(payload)


class TestOutputValidateNodeAudit:
    def test_success_path_emits_domain_event(self, monkeypatch):
        calls = _recorder(monkeypatch, post_mod)
        state = {"alert_report": "1 at-risk SKU found, 1 high-risk.", "correlation_id": "corr-3"}
        r = OutputValidateNode().execute(state)

        assert r["status"] == AgentStatus.SUCCESS.value
        assert [c[0] for c in calls] == ["alert_report_output_validated"]
        payload = calls[0][1]
        assert payload["correlation_id"] == "corr-3"
        assert payload["blocked"] is False
        assert payload["report_length"] == len(state["alert_report"])
        _assert_non_sensitive(payload)

    def test_blocked_path_emits_domain_event(self, monkeypatch):
        calls = _recorder(monkeypatch, post_mod)
        r = OutputValidateNode().execute({"alert_report": "unit price: $50 for this SKU"})

        assert r["status"] == AgentStatus.ERROR.value
        assert [c[0] for c in calls] == ["alert_report_output_validated"]
        assert calls[0][1]["blocked"] is True
        # Payload carries flags only - never the offending report body.
        _assert_non_sensitive(calls[0][1])

    def test_s3_recheck_block_emits_domain_event(self, monkeypatch):
        calls = _recorder(monkeypatch, post_mod)
        out = OutputValidateNode()._extra_security_gate_output({"alert_report": "margin: 20% on this supplier"})

        assert out["status"] == AgentStatus.ERROR.value
        assert [c[0] for c in calls] == ["output_validate_s3_recheck_blocked"]
