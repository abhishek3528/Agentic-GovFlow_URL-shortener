# Testing

## Current result

The required command is:

```powershell
D:\URL-Project\.venv\Scripts\python.exe -m pytest tests/ -q
```

It collects and passes **218 tests**. **106** are in
`tests/test_governance_negative.py`.

## Coverage by file

| File | Tests | What it covers |
|---|---:|---|
| `tests/test_agents.py` | 5 | Two-level capability/task dispatch, missing-agent and missing-handler fail-closed behavior, engine-level typo rejection, and per-agent event attribution. |
| `tests/test_contracts.py` | 26 | Frozen model invariants, complete task/run transition tables, human approval validation, deterministic clock/ids, context and plan versioning, artifact mutability boundary, and deterministic/failure executors. |
| `tests/test_controls.py` | 17 | Allow and deny paths for every named policy, unknown-policy fail-closed behavior, correlated approvals, transient recovery, governed fallback output/gates, invalid fallback fail-closed behavior, compensation, and safe-stop. |
| `tests/test_graph.py` | 3 | Deterministic topology/frontiers/descendants, cycle and unknown-dependency rejection, and artifact-producer ancestry. |
| `tests/test_planner.py` | 13 | Requirement-driven decomposition: signal rules for persistence, security, brownfield defect, breaking change and analytics work; validation of every derived plan through `DependencyGraph`; cross-process determinism; and reproduction of S-01's existing plan from its requirement text. |
| `tests/test_engine.py` | 7 | Fork/join execution, illegal and direct transition rejection, missing join output, missing gate evaluator, deterministic replay, and selective re-plan history. |
| `tests/test_events.py` | 4 | Per-run sequence assignment, append-only JSONL rehydration, rejected replay/gaps, and deep immutability of stored history. |
| `tests/test_governance_negative.py` | 106 | Independent adversarial checks for state, approval (scripted and interactive), join, freshness, recovery, fallback, policies, append-only history, selective re-planning, and the product/control-plane import boundary, including load-bearing controls. |
| `tests/test_greenfield_scenario.py` | 4 | S-01 fork/join, human release gate, policy denial with permitted work, transient recovery, event-derived metrics, evidence export, and incomplete-requirement denial. |
| `tests/test_brownfield_scenario.py` | 4 | S-02 repository impact before planning, red-to-green proof, gated high-impact fix, retry exhaustion, compensation-before-safe-stop, exact metrics, and evidence export. |
| `tests/test_ambiguous_scenario.py` | 5 | S-03 ambiguity pause, parallel planning join, v1/v2 history, exact selective invalidation result, re-applied policies/gates, human quality approval, and exported re-plan evidence. |
| `tests/test_scenario_runner.py` | 6 | Canonical indexed evidence, content hashes, reproducible export, dependency ordering, run-id isolation, and safe evidence paths. |
| `tests/test_shortener_service.py` | 6 | Idempotency, deterministic and bounded collision search, SQLite restart persistence, privacy-safe click schema, and UTC-day analytics. |
| `tests/test_shortener_api.py` | 11 | HTTP create/redirect/stats, idempotency, collision, invalid/unsafe input, not-found, health/readiness, and OpenAPI. |
| `tests/test_shortener_live.py` | 1 | Real Uvicorn process startup and the live create -> redirect -> stats smoke path. |

The counts above sum to 218 and include parametrized cases as pytest collects
them.

## Lane D: proving controls are load-bearing

The strongest tests do more than observe a denial. They pair the protected case
with a `test_x_control_is_load_bearing` counterpart. The first test proves unsafe
or incomplete work is stopped; the counterpart changes only the relevant control
condition and proves valid work proceeds. This prevents vacuous implementations
such as “deny everything,” “never retry,” or “invalidate every branch” from
passing the negative suite.

Representative pairs are:

| Protected behavior | Adversarial test | Load-bearing counterpart |
|---|---|---|
| Contract rejects illegal task transitions while allowing legal ones | `test_illegal_task_transition_rejected_by_contract` | `test_illegal_task_transition_control_is_load_bearing` |
| Engine enforces the transition predicate | `test_engine_refuses_an_illegal_task_transition_without_touching_state` | `test_engine_illegal_transition_control_is_load_bearing` |
| Only a human can construct/grant approval | `test_approval_construction_rejects_a_non_human_actor` and `test_engine_refuses_an_agent_granted_approval` | `test_approval_construction_control_is_load_bearing` and `test_approval_from_a_human_control_is_load_bearing` |
| A join needs every branch output | `test_join_does_not_advance_when_a_branch_produces_no_output` | `test_join_missing_branch_output_control_is_load_bearing` |
| A stale artifact cannot be consumed | `test_consumer_cannot_execute_while_its_input_is_invalidated` | `test_stale_input_control_is_load_bearing` |
| Retry exhaustion safe-stops, while recoverable work retries | `test_retry_exhaustion_safe_stops_and_never_claims_success` | `test_retry_budget_control_is_load_bearing` |
| Unsafe URLs are denied while safe URLs and safe sibling work proceed | `test_url_safety_policy_denies_a_prohibited_destination` and `test_a_denied_task_is_blocked_while_permitted_work_completes` | `test_url_safety_control_is_load_bearing` and `test_policy_denial_control_is_load_bearing` |
| An interactive approval refuses a non-terminal input rather than reading piped text, and fails closed on EOF | `test_the_interactive_provider_refuses_a_non_terminal_input` and `test_the_interactive_provider_fails_closed_when_input_ends` | `test_interactive_approval_control_is_load_bearing` |
| A human denial blocks the release instead of crashing or quietly succeeding | `test_a_human_denial_through_the_provider_blocks_the_release` | `test_interactive_approval_control_is_load_bearing` |
| A task's declared capability must be owned by a registered agent, or nothing runs | `test_an_unknown_capability_fails_closed` and `test_a_registered_agent_without_the_handler_fails_closed` | `test_capability_dispatch_control_is_load_bearing` |
| Naming an agent to look like a person confers no human authority | `test_an_agent_cannot_attribute_its_work_to_a_human` | `test_approval_from_a_human_control_is_load_bearing` |
| The product never depends on the control plane that governs it | `test_the_service_does_not_import_the_orchestrator` | `test_import_boundary_check_is_load_bearing` |
| A fallback is governed work, not an escape hatch — it cannot skip an exit gate | `test_a_fallback_cannot_bypass_an_exit_gate` | `test_fallback_exit_gate_control_is_load_bearing` |
| Retry is exhausted before a fallback is attempted | `test_a_fallback_is_not_attempted_while_retries_remain` | `test_fallback_ordering_control_is_load_bearing` |
| Re-plan blast radius follows dependency lineage | `test_replan_invalidates_only_affected_descendants` | `test_selective_invalidation_control_is_load_bearing` |
| Event storage rejects replay/gaps but accepts the next record | `test_event_store_refuses_a_replayed_or_skipped_sequence` | `test_event_store_append_control_is_load_bearing` |

All names in this table are from `tests/test_governance_negative.py`. Lane D was
kept independent of the implementation lanes and is intentionally not modified
as part of final documentation work.

## Scenario assertions worth reviewing first

- S-01 fork/join and release approval:
  `tests/test_greenfield_scenario.py::test_s01_executes_full_sdlc_fork_join_and_human_release_gate`.
- S-02 compensation precedes safe-stop and metrics are exactly
  `retry_count=1`, `rollback_count=1`, `mttr_seconds=5.0`:
  `tests/test_brownfield_scenario.py::test_s02_exhausts_retry_compensates_and_safe_stops_with_metrics`.
- S-03 retains context v1 beside v2, retains both plan revisions, and ends with
  5 stale plus 14 fresh artifacts:
  `tests/test_ambiguous_scenario.py::test_s03_replan_preserves_v1_and_selectively_invalidates_analytics`.
- Metrics can be recomputed solely from events:
  `tests/test_governance_negative.py::test_metrics_are_reproducible_from_the_event_stream_alone`.
- Evidence is canonical, indexed, hashed, and reproducible:
  `tests/test_scenario_runner.py::test_exporter_writes_indexed_reproducible_bundle`.
