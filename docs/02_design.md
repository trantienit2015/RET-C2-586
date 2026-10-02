# Template Design Specification

## Position in AgentCore Architecture

- **Agent Class**: `RETSupplierLeadTimeAnomalyAlertAgent`
- **L1 Base**: AgentBaseGraph
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution)

## Architecture Overview

Flat `AgentBaseGraph` with the fixed 5-node backbone. No GraphNode
composition needed — a single linear Tool-call chain (query DB → compare
→ classify → synthesize) with no nested domain workflow requiring
subgraph isolation.

### Node Configuration

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | schema_version/session_id/trust_level | - | - | InitializeNode (default) |
| pre_process | `InputValidateSupplierDataQueryNode` — S-1/S-2 gate + Tool-call to supplier DB | `user_input` | `query_scope`, `supplier_records` | FunctionNode |
| main | `LeadTimeCompareRiskClassifyNode` — deterministic delay/risk computation + LLM-optional alert synthesis | `supplier_records` | `at_risk_skus`, `alert_report` | FunctionNode |
| post_process | `OutputValidateNode` — non-suppressible contract-term redaction re-check | `alert_report` | (validated) | FunctionNode |
| finalize | response_metadata, total_time_ms | - | - | FinalizeNode (default) |

### Data Flow

```
START → initialize → pre_process → main → {route} → post_process → finalize → END
                                            ↓ (retry)
                                          pre_process
```

### State Definition

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| `query_scope` | str (JSON dict) | `{supplier_ids, category, date_range}` parsed from user_input | Yes |
| `supplier_records` | str (JSON list[dict]) | raw records from the supplier DB Tool query | Yes |
| `at_risk_skus` | str (JSON list[dict]) | risk-classified SKUs `{sku_id, supplier, category, delay_days, risk_tier, recommended_action}` | Yes |
| `alert_report` | str | final alert narrative (contract-term/pricing/margin data always redacted) | Yes |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State (checkpoint DB leakage)
- InvocationContext via `config["configurable"]` only (not in State)
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, permissions, credential handle)
- [x] SecurityViolationError
- [x] S-1: `required_trust_level = INTERNAL` on every node — supply-chain
      data is internal business data; authenticated internal callers
      (buyers/supply chain managers) only, never anonymous.
- [x] S-3: `_extra_security_gate_output()` — non-suppressible re-check on
      `OutputValidateNode` that `alert_report` contains no raw contract
      terms/pricing/margin data. Own-dict field re-check only, per the
      S-3 hook self-consistency rule.
- [x] S-4: `emit_trace_event()` — `output_validate_s3_recheck_blocked`
      inside `_extra_security_gate_output()`.
- [x] Secrets: production `supplier_db_connector` Tool integration reads
      the DB access token via `ctx.secrets.require("SUPPLIER_DB_TOKEN")`
      (this MR ships the deterministic-core pipeline with `db_records`
      injected via config for testability; live connector wiring is a
      follow-up).

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass → framework `@final` gate always runs automatically;
>   extend via `_extra_security_gate_input()` / `_extra_security_gate_output()` only

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: framework/ and shared/ only (no agents/base/ required)

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| Level 2 pattern | VectorRAGAgent | ToolCallingAgent | ToolCallingAgent | Per the pattern rationale: the supplier/PO system is a live structured DB, not a vector KB — VectorRAGAgent is the wrong pattern for structured DB queries |
| Composition pattern | GraphNode (subgraph) | Flat 3-node (pre_process/main/post_process) | Flat | A single linear Tool-call chain with no nested domain-specific workflow requiring subgraph isolation |
| Lead-time comparison / risk classification | LLM-based | Deterministic (Tool + rule table) | Deterministic | Date-delta computation and `(delay_days x category_type) -> risk_tier` are pure/config-driven computations — no LLM needed; the LLM-essential value is the contextual alert narrative, not the classification itself |
| Alert narrative synthesis | Deterministic template-fill only | LLM-optional with deterministic fallback | LLM-optional | A flat template-fill cannot produce contextual category-level risk narrative across 50+ suppliers — LLM is the differentiating value; deterministic fallback keeps the pipeline runnable without an LLM |
| Node flow mapping | 6 separate nodes matching the original draft exactly | Combine InputValidate+SupplierDataQuery (pre_process); LeadTimeCompare+RiskClassify+AlertReportFormat (main) | Combined groups | Each combined group operates on the same input with no branching/retry boundary between the sub-steps |
| S-1 trust level | VERIFIED_EXTERNAL | INTERNAL | INTERNAL | Per the security spec: supply-chain data is internal business data, authenticated internal callers only |

## 1. Deployment entry points

The agent is reachable through two entry points, and both resolve their runtime inputs the same way.

| Entry point | File | Used by |
|---|---|---|
| Standalone HTTP | `src/api/server.py` | staging rehearsal, direct invocation |
| Marketplace one-shot Pod | `cli.py` | the platform runner (container `CMD`) |

Both read runtime parameters from `config/config.yaml` — the manifest carries discovery metadata
only — and both scope secrets to the same location (`namespace=ret`, `agent_name=ret-c2-586`), so a
secret provisioned for one path resolves identically on the other.

**Deployment-provided LLM client.** The three Azure OpenAI connection values
(`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT` — the bare resource endpoint — and
`AZURE_OPENAI_DEPLOYMENT`) are resolved through the agent's own secret provider, never from the
process environment. Both entry points build the client only when all three resolve and pass it in
as `config["llm"]`; when any is absent the key stays unset and the graph keeps its deterministic
path. The manifest declares the three secrets and the provider extra.

**Supplier data source, and its non-production stub.** Supplier lead-time records are supplied by
the deploying environment; this agent carries none of its own. The connector is built from that
environment's own credential (`SUPPLIER_DB_TOKEN`, `SUPPLIER_DB_URL`), resolved through the agent's
secret provider. A production deployment that cannot build it does not start, so a missing
credential surfaces at deployment time rather than on a caller's first request.

A non-production run may select a stub connector instead, and only when **both** conditions hold:
the environment declares itself non-production (`APP_ENV` is `test`, `local` or `dev`) **and** the
adapter's own flag (`RET_C2_586_ALLOW_STUB_SUPPLIER_DB`) is set. A production or unset `APP_ENV`
fails closed regardless of the flag, so a single stray variable can never select the stub.

The stub holds no records, so a staging rehearsal that selects it returns `status: success` with
"No at-risk SKUs found for the given query scope" — the pipeline ran to completion over an empty
record set. **That result attests the execution path, not the detection logic.** It must never be
read as evidence that a real supplier data set contained no at-risk SKUs; only a run against a
provisioned supplier data source can support that claim.

**Progress events.** Each pipeline stage emits a non-terminal progress event at its start, so a
caller sees the run advancing. Outside the Marketplace runtime the emitter resolves to a no-op, so
the same code is safe on every path. Terminal events belong to the platform runner and are never
emitted by this agent.

**Caller authentication at the standalone entry point.** A caller that no upstream middleware
vouched for stays anonymous unless it presents a deployment-level credential: the external bearer
token grants the verified-external level, and a separate staging-only runner credential is the only
way to reach the internal level. Trust established upstream is never changed.
