"""AgentCore Platform v1.0 - RET-C2-586 state schema.

Retail Supplier Lead Time Anomaly Detection & Supply Risk Alert
(ToolCallingAgent pattern - live structured supplier/PO DB query, not KB).
Flat TypedDict extension of AgentState (ADR-005). Structured payloads
(dict/list) are JSON-string-encoded before being stored in state fields.
"""

import json
from typing import Any, NotRequired

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def from_json(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class State(AgentState):
    """Agent state for RET-C2-586.

    All agent-specific fields are wrapped in NotRequired[...] (state-safety): a
    field may be absent from a mid-pipeline checkpoint, and a bare
    (non-NotRequired) field would KeyError when a downstream node reads it
    before it is written.

    query_scope: JSON dict - {supplier_ids, category, date_range} parsed from user_input.
    supplier_records: JSON list[dict] - raw records from supplier_db_connector Tool.
    at_risk_skus: JSON list[dict] - risk-classified SKUs {sku_id, supplier, category,
      delay_days, risk_tier, recommended_action}.
    alert_report: str - final LLM-synthesized alert narrative + structured risk data.
    """

    query_scope: NotRequired[str]
    supplier_records: NotRequired[str]
    at_risk_skus: NotRequired[str]
    alert_report: NotRequired[str]
