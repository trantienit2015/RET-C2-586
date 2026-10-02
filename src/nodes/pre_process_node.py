"""AgentCore Platform v1.0 - RET-C2-586 InputValidateSupplierDataQueryNode (pre_process).

Combines InputValidate (S-1 + S-2) + SupplierDataQuery (Tool-call to the
supplier DB) per the the original draft's node flow, since both operate on the
raw input with no branching between them.
"""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from shared.services.events import emitter
from shared.services.events.types import EventType
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json
from src.services.service import SupplierDBClient, query_supplier_db
from src.services.supplier_db_adapter import SupplierDBRequestError


class InputValidateSupplierDataQueryNode(FunctionNode):
    """Validate the query scope and fetch matching supplier records."""

    # Supply-chain data is internal business data - authenticated internal
    # callers (buyers/supply chain managers) only, never anonymous.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.INTERNAL

    def __init__(
        self,
        db_records: list[dict[str, Any]] | None = None,
        client: "SupplierDBClient | None" = None,
    ) -> None:
        super().__init__()
        self._db_records = db_records or []
        # Live supplier DB client (built from the bound secret provider at the
        # entry point). When both client and db_records are absent the data
        # source is unavailable and the node must ERROR, never return
        # a false-negative empty success.
        self._client = client

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # Progress (non-terminal) event for the caller. Outside the Marketplace runtime
        # this resolves to a no-op emitter, so it is safe on every entry path.
        emitter().emit_event(
            event_type=EventType.PROGRESS_UPDATE,
            message="Checking the request...",
            metadata={"step": "pre_process"},
        )
        user_input = state.get("user_input", "")

        # S-4: domain audit event on every path (counts/flags only, no raw input/PII).
        emit_trace_event(
            "supplier_query_received",
            {
                "correlation_id": state.get("correlation_id"),
                "has_input": bool(user_input and user_input.strip()),
                "live_client": self._client is not None,
            },
            state,
        )

        if not user_input or not user_input.strip():
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["InputValidateSupplierDataQueryNode: user_input is empty or missing"],
            }

        query_scope = from_json(user_input, None)
        if not isinstance(query_scope, dict):
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["InputValidateSupplierDataQueryNode: user_input must be a JSON object query scope"],
            }

        # The supplier data source must be available. With neither a
        # live client nor injected records there is nothing to query — treat
        # this as an unavailable data source (explicit ERROR), never a
        # false-negative "no at-risk SKUs" success.
        if self._client is None and not self._db_records:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "InputValidateSupplierDataQueryNode: supplier DB unavailable — "
                    "no connector configured and no records injected"
                ],
            }

        try:
            supplier_records = query_supplier_db(query_scope, db_records=self._db_records, client=self._client)
        except SupplierDBRequestError as exc:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"InputValidateSupplierDataQueryNode: supplier DB query failed: {exc}"],
            }

        return {
            "query_scope": to_json(query_scope),
            "supplier_records": to_json(supplier_records),
            "status": AgentStatus.SUCCESS.value,
        }
