# RET-C2-586 — Test Specification

## Test Strategy

Deterministic-core: no LLM required, full suite runs offline. Alert
narrative synthesis accepts an optional `llm` client and falls back to
deterministic citation-based synthesis when absent.

## Unit Tests (`tests/unit/test_nodes.py`)

| Node | Cases |
|---|---|
| InputValidateSupplierDataQueryNode | success (query scope parsed, records fetched); empty user_input→ERROR; not a JSON object→ERROR |
| LeadTimeCompareRiskClassifyNode | success (high-risk SKU on high-impact category); success (no delay, no at-risk SKUs); missing supplier_records→ERROR |
| OutputValidateNode | success (compliant report); raw contract term present→ERROR (non-suppressible hook) |

## Integration Tests (`tests/integration/test_graph.py`)

| ID | Test | Expected |
|---|---|---|
| I-1 | query scope with a delayed high-impact-category SKU | pipeline completes (≥4 nodes); alert report flags high risk |
| I-2 | query scope with no delayed SKUs | pipeline completes; alert report says no at-risk SKUs |
| I-3 | empty user_input | ERROR |

> Per the S-2 gate's effect on the success path, integration tests
> assert pipeline-completion invariants rather than an exact terminal status
> for success cases — unit tests already pin the success-path logic
> deterministically.

## Proof-of-Boundary Tests (`tests/proof_of_boundary/`)

| PB-ID | Boundary | Test | Expected Result |
|-------|----------|------|----------------|
| PB-1 | BaseNode → EventEmitter | `emit_trace_event()` fires on every invocation path | No silent failures |
| PB-2 | State serialization | Post-invoke State is primitives only | No Pydantic/dataclass |
| PB-4 | Import isolation | No Level 0 imports | AST scan: 0 violations |
| PB-5 | Checkpoint safety | No JWT/Pydantic in checkpoint | Inspection pass |
| PB-6 | Invoke execution order | S-1 → node_start → S-2 → execute → S-3 → node_complete | Order verified |

## Business Logic Tests

| TC-ID | Test | Input | Expected Result |
|-------|------|-------|----------------|
| BL-01 | high-risk classification | 14+ day delay on a high-impact category (frozen/seasonal/cold-chain) | risk_tier=H |
| BL-02 | medium-risk classification | 5-13 day delay on a standard category | risk_tier=M |
| BL-03 | low-risk / no alert | delay ≤ 0 days | SKU excluded from at_risk_skus |
| BL-04 | non-suppressible contract-term redaction | alert text contains "unit price: $50" or "margin: 20%" | text replaced with `[REDACTED_CONTRACT_TERM]` |

## Non-suppressible output tests (critical)

- `alert_report` must never contain raw contract terms/pricing/margin data
  (unit + I-1/I-2 + BL-04).
- The post_process S-3 hook re-checks `alert_report`'s own field only
  (S-3 hook self-consistency rule) — never cross-references separate state
  fields read independently.

## Test Execution Summary
- Execution date: 2026-07-08
- Total tests: see CI `run-tests` job output
- Coverage: deterministic-core, no LLM dependency

## 1. Deployment path tests

### TC-DEP-01 Marketplace entry point identity — `tests/unit/test_cli_entry_point.py`

| Case | Expected |
|---|---|
| Identity is concrete | `agent_name` / `namespace` are non-empty and not the runner default |
| Identity matches the HTTP entry point | the values equal what `src/api/server.py` passes to `secrets_factory`; where that entry point provisions no secrets, they equal the manifest `namespace` and the lower-cased template id |
| The runner call uses the constants | `run_agent_marketplace` is called with the module constants, not inline literals |

## 2. Staging rehearsal expectation (stub supplier data source)

### TC-STG-01 Empty stub result completes the pipeline

**Setup.** `APP_ENV=test` and `RET_C2_586_ALLOW_STUB_SUPPLIER_DB=1`, so the adapter selects the
stub connector (both gates required — see `tests/unit/test_server_stub_gate.py`). No supplier
credential is provisioned.

**Input.** `deploy/invoke_payload.json` — a JSON query scope (`supplier_ids`, `sku_categories`,
`lookback_days`) matching the contract `InputValidateSupplierDataQueryNode` enforces.

**Expected result.** `status: success`, output "No at-risk SKUs found for the given query scope".
The stub holds no records, so an empty detection result is the correct completion, not a defect,
and the declared expected status for a staging rehearsal is therefore `success`.

**What this does and does not attest.** It attests that the entry point starts, that the query
scope passes input validation, and that the pipeline runs end to end. **It does not attest the
detection logic** — that is covered by the unit and business-logic tests above, which drive the
comparison and classification steps with records directly.
