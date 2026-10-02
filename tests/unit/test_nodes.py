# RET-C2-586 - Unit tests: per-node success + error/edge paths.

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
    {
        "sku_id": "SKU-002",
        "supplier_id": "SUP-B",
        "category": "dry_goods",
        "actual_delivery_date": "2026-07-01",
        "contracted_delivery_date": "2026-07-01",
    },
]


class TestInputValidateSupplierDataQueryNode:
    def test_success(self):
        state = {"user_input": to_json({"supplier_ids": ["SUP-A"]})}
        r = InputValidateSupplierDataQueryNode(db_records=DB_RECORDS).execute(state)
        assert r["status"] == AgentStatus.SUCCESS

    def test_empty_input_error(self):
        assert InputValidateSupplierDataQueryNode().execute({"user_input": ""})["status"] == AgentStatus.ERROR

    def test_not_json_object_error(self):
        assert InputValidateSupplierDataQueryNode().execute({"user_input": "not json"})["status"] == AgentStatus.ERROR


class TestLeadTimeCompareRiskClassifyNode:
    def test_success_high_risk_on_frozen_category(self):
        state = {"supplier_records": to_json(DB_RECORDS)}
        r = LeadTimeCompareRiskClassifyNode().execute(state)
        assert r["status"] == AgentStatus.SUCCESS

    def test_success_no_at_risk_skus(self):
        no_delay_records = [DB_RECORDS[1]]
        state = {"supplier_records": to_json(no_delay_records)}
        r = LeadTimeCompareRiskClassifyNode().execute(state)
        assert r["status"] == AgentStatus.SUCCESS
        assert "No at-risk SKUs" in r["alert_report"]

    def test_missing_records_error(self):
        assert LeadTimeCompareRiskClassifyNode().execute({"supplier_records": ""})["status"] == AgentStatus.ERROR

    def test_success_null_category_does_not_crash(self):
        # A record with the "category" key present but null (distinct from a
        # missing key) must not crash classify_risk_tier's category.lower().
        records = [
            {
                "sku_id": "SKU-003",
                "supplier_id": "SUP-C",
                "category": None,
                "actual_delivery_date": "2026-07-20",
                "contracted_delivery_date": "2026-07-01",
            }
        ]
        state = {"supplier_records": to_json(records)}
        r = LeadTimeCompareRiskClassifyNode().execute(state)
        assert r["status"] == AgentStatus.SUCCESS

    def test_configured_llm_raises_returns_error_not_fallback(self):
        # Recipe 3m half-fix trap: a configured-but-failing LLM must return
        # ERROR, not silently substitute the deterministic narrative.
        class RaisingLLM:
            def complete(self, messages):
                raise ConnectionError("provider unreachable")

        state = {"supplier_records": to_json(DB_RECORDS)}
        r = LeadTimeCompareRiskClassifyNode(llm=RaisingLLM()).execute(state)
        assert r["status"] == AgentStatus.ERROR

    def test_configured_llm_empty_response_returns_error_not_fallback(self):
        class EmptyLLM:
            def complete(self, messages):
                return {"content": ""}

        state = {"supplier_records": to_json(DB_RECORDS)}
        r = LeadTimeCompareRiskClassifyNode(llm=EmptyLLM()).execute(state)
        assert r["status"] == AgentStatus.ERROR

    def test_no_llm_configured_uses_deterministic_fallback(self):
        # llm=None (deterministic-core mode) is the only valid fallback path.
        state = {"supplier_records": to_json(DB_RECORDS)}
        r = LeadTimeCompareRiskClassifyNode(llm=None).execute(state)
        assert r["status"] == AgentStatus.SUCCESS


class TestOutputValidateNode:
    def test_success_compliant_report(self):
        state = {"alert_report": "1 at-risk SKU found, 1 high-risk."}
        r = OutputValidateNode().execute(state)
        assert r["status"] == AgentStatus.SUCCESS

    def test_contract_term_error(self):
        state = {"alert_report": "unit price: $50 for this SKU"}
        assert OutputValidateNode().execute(state)["status"] == AgentStatus.ERROR

    def test_extra_gate_blocks_contract_term(self):
        bad_state = {"alert_report": "margin: 20% on this supplier"}
        out = OutputValidateNode()._extra_security_gate_output(bad_state)
        assert out["status"] == AgentStatus.ERROR

    def test_extra_gate_passthrough_compliant_report(self):
        good_state = {"alert_report": "1 at-risk SKU, risk=H, action: escalate"}
        assert OutputValidateNode()._extra_security_gate_output(good_state) is good_state
