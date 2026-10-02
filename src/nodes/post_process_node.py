"""AgentCore Platform v1.0 - RET-C2-586 OutputValidateNode (post_process).

S-3 output gate: non-suppressible re-check that alert_report contains no
raw contract terms/pricing/margin data. Own-dict field re-check only
(S-3 self-consistency rule - s3 hook cross state recheck fragile).
"""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from shared.services.events import emitter
from shared.services.events.types import EventType
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.service import strip_contract_terms


class OutputValidateNode(FunctionNode):
    """Validate the alert report contains no raw contract terms/pricing/margin data."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.INTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # Progress (non-terminal) event for the caller. Outside the Marketplace runtime
        # this resolves to a no-op emitter, so it is safe on every entry path.
        emitter().emit_event(
            event_type=EventType.PROGRESS_UPDATE,
            message="Preparing the result...",
            metadata={"step": "post_process"},
        )
        alert_report = state.get("alert_report", "")

        _, blocked = strip_contract_terms(alert_report)

        # S-4: domain audit event on every path (flags/lengths only, never the
        # report body, contract terms, pricing or margin values).
        emit_trace_event(
            "alert_report_output_validated",
            {
                "correlation_id": state.get("correlation_id"),
                "has_report": bool(alert_report),
                "report_length": len(alert_report),
                "blocked": blocked,
            },
            state,
        )

        if blocked:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["OutputValidateNode: alert report contains raw contract terms/pricing/margin data"],
            }

        return {
            "formatted_output": alert_report,
            "status": AgentStatus.SUCCESS.value,
        }

    def _extra_security_gate_output(self, state: dict[str, Any]) -> dict[str, Any]:
        """Non-suppressible re-check on the output dict's own field."""
        alert_report = state.get("alert_report", "")
        _, blocked = strip_contract_terms(alert_report)
        if blocked:
            emit_trace_event("output_validate_s3_recheck_blocked", {}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "OutputValidateNode: S-3 re-check blocked an alert report containing raw contract terms/pricing/margin data"
                ],
            }
        return state
