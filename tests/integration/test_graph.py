# RET-C2-586 - Integration test: full graph compile + invoke.
#
# An oracle that accepts status in ("success", "error", "cancelled") and
# asserts only history length would pass even if the advertised business path
# failed. These tests pin the successful business path (status == success, non-empty risk content) and
# add explicit connector-unavailable / connector-failure assertions.

import json

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph
from src.services.supplier_db_adapter import SupplierDBRequestError

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

DELAYED_QUERY = json.dumps({"supplier_ids": ["SUP-A"]})
NO_DELAY_QUERY = json.dumps({"supplier_ids": ["SUP-B"]})
EMPTY_QUERY = ""


def _ctx(session_id: str) -> InvocationContext:
    return InvocationContext(
        session_id=session_id, caller_trust_level=TrustLevel.INTERNAL, caller_id="buyer-001"
    )


class _RaisingClient:
    """Live-client stand-in that fails the query (connector unavailable)."""

    def query(self, query_scope):
        raise SupplierDBRequestError("supplier DB timeout")


class TestAgentIntegration:
    def test_delayed_query_produces_alert(self):
        agent = Graph(config={"max_retry": 1, "db_records": DB_RECORDS})
        agent.compile()
        result = agent.invoke(DELAYED_QUERY, ctx=_ctx("it-1"))

        # Pin the advertised business path, not merely completion.
        assert result["status"] == "success"
        # AgentBaseGraph surfaces the post_process output string under "output".
        report = result.get("output") or ""
        assert report.strip(), "successful delayed query must emit a non-empty alert report"
        # SUP-A frozen SKU is 14 days late → must not be the empty false negative.
        assert "No at-risk SKUs found" not in report

    def test_no_delay_query_completes_success(self):
        agent = Graph(config={"max_retry": 1, "db_records": DB_RECORDS})
        agent.compile()
        result = agent.invoke(NO_DELAY_QUERY, ctx=_ctx("it-2"))

        # An on-time supplier is a legitimate empty-risk success (distinct from
        # an unavailable data source).
        assert result["status"] == "success"

    def test_empty_query_error(self):
        agent = Graph(config={"max_retry": 1, "db_records": DB_RECORDS})
        agent.compile()
        result = agent.invoke(EMPTY_QUERY, ctx=_ctx("it-3"))
        assert result["status"] == "error"

    def test_data_source_unavailable_errors(self):
        # No live client AND no injected records → the supplier data
        # source is unavailable; the pipeline must ERROR, never return a
        # false-negative 'no at-risk SKUs' success.
        agent = Graph(config={"max_retry": 1})
        agent.compile()
        result = agent.invoke(DELAYED_QUERY, ctx=_ctx("it-4"))
        assert result["status"] == "error"

    def test_connector_failure_errors(self):
        # A live connector that fails its query must surface ERROR,
        # not an empty success.
        agent = Graph(config={"max_retry": 1, "supplier_db_client": _RaisingClient()})
        agent.compile()
        result = agent.invoke(DELAYED_QUERY, ctx=_ctx("it-5"))
        assert result["status"] == "error"
