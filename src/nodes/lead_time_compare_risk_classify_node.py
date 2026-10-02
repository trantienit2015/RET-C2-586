"""AgentCore Platform v1.0 - RET-C2-586 LeadTimeCompareRiskClassifyNode (main).

Combines LeadTimeCompare (deterministic date-delta vs SLA) + RiskClassify
(deterministic rule table) + AlertReportFormat (LLM-optional grounded
synthesis) per the the original draft's node flow, since all three operate on
the same supplier_records input with no branching between them.
"""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from shared.services.events import emitter
from shared.services.events.types import EventType
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json
from src.services.service import (
    AlertReportGenerationError,
    classify_risk_tier,
    compute_delay_days,
    format_alert_report,
    recommend_action,
)


class LeadTimeCompareRiskClassifyNode(FunctionNode):
    """Compute delivery delay, classify supply-risk tier, and synthesize the alert report."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.INTERNAL

    def __init__(self, llm: Any | None = None) -> None:
        super().__init__()
        self._llm = llm

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # Progress (non-terminal) event for the caller. Outside the Marketplace runtime
        # this resolves to a no-op emitter, so it is safe on every entry path.
        emitter().emit_event(
            event_type=EventType.PROGRESS_UPDATE,
            message="Working on the request...",
            metadata={"step": "main"},
        )
        supplier_records = from_json(state.get("supplier_records", ""), None)

        if not isinstance(supplier_records, list):
            # S-4: domain audit event on the early-error path too (flags only,
            # no supplier/contract content).
            emit_trace_event(
                "lead_time_risk_input_rejected",
                {
                    "correlation_id": state.get("correlation_id"),
                    "reason": "supplier_records_missing_or_invalid",
                },
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["LeadTimeCompareRiskClassifyNode: supplier_records missing or invalid"],
            }

        at_risk_skus = []
        for record in supplier_records:
            try:
                delay_days = compute_delay_days(record["actual_delivery_date"], record["contracted_delivery_date"])
            except (KeyError, ValueError):
                continue
            if delay_days <= 0:
                continue
            risk_tier = classify_risk_tier(delay_days, record.get("category", ""))
            at_risk_skus.append(
                {
                    "sku_id": record.get("sku_id"),
                    "supplier": record.get("supplier_id"),
                    "category": record.get("category"),
                    "delay_days": delay_days,
                    "risk_tier": risk_tier,
                    "recommended_action": recommend_action(risk_tier),
                }
            )

        try:
            alert_report = format_alert_report(at_risk_skus, llm=self._llm)
        except AlertReportGenerationError as exc:
            # Configured-but-failing LLM must surface ERROR, not silently
            # substitute the deterministic fallback (recipe 3m half-fix trap).
            emit_trace_event(
                "lead_time_risk_alert_generation_failed",
                {
                    "correlation_id": state.get("correlation_id"),
                    "at_risk": len(at_risk_skus),
                },
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"LeadTimeCompareRiskClassifyNode: alert report generation failed: {exc}"],
            }

        # S-4: domain audit event (counts only, no supplier/contract content).
        emit_trace_event(
            "lead_time_risk_classified",
            {
                "correlation_id": state.get("correlation_id"),
                "records": len(supplier_records),
                "at_risk": len(at_risk_skus),
                "high_risk": sum(1 for s in at_risk_skus if s.get("risk_tier") == "H"),
            },
            state,
        )

        return {
            "at_risk_skus": to_json(at_risk_skus),
            "alert_report": alert_report,
            "status": AgentStatus.SUCCESS.value,
        }
