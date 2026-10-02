"""AgentCore Platform v1.0 - RET-C2-586 outer graph (Cat 2).

Flat AgentBaseGraph with the fixed 5-node backbone. No GraphNode
composition needed - a single linear Tool-call chain (query DB -> compare
-> classify -> synthesize) with no nested domain workflow requiring
subgraph isolation.

Backbone: initialize -> pre_process(InputValidateSupplierDataQuery) ->
          main(LeadTimeCompareRiskClassify) -> post_process(OutputValidate)
          -> finalize
"""

from framework.graph.agent_base_graph import AgentBaseGraph

from src.nodes.lead_time_compare_risk_classify_node import LeadTimeCompareRiskClassifyNode
from src.nodes.post_process_node import OutputValidateNode
from src.nodes.pre_process_node import InputValidateSupplierDataQueryNode
from src.schemas.state import State


class RETSupplierLeadTimeAnomalyAlertAgent(AgentBaseGraph):
    """RET-C2-586 - Retail Supplier Lead Time Anomaly Detection & Supply Risk Alert (Cat 2)."""

    @property
    def name(self) -> str:
        return "ret-c2-586"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects initialize + finalize

        db_records = self.config.get("db_records")
        supplier_db_client = self.config.get("supplier_db_client")
        llm = self.config.get("llm")

        self._nodes["pre_process"] = InputValidateSupplierDataQueryNode(
            db_records=db_records, client=supplier_db_client
        )
        self._nodes["main"] = LeadTimeCompareRiskClassifyNode(llm=llm)
        self._nodes["post_process"] = OutputValidateNode()

    # add_edges() is NOT overridden - backbone wiring belongs to the framework.


# Stable alias for the standalone entry point import (src/api/server.py).
Graph = RETSupplierLeadTimeAnomalyAlertAgent
