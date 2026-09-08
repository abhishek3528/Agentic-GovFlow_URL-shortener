# Architecture

## Governing idea

The repository separates decisions about work from the work itself:

| Area | Responsibility |
|---|---|
| `orchestrator/` | Owns plans, legal state transitions, readiness, gates, policies, approvals, artifact provenance, recovery, re-planning, and events. |
| `orchestrator/executor.py` | Defines the narrow `TaskExecutor.execute(task, inputs) -> TaskOutput` work seam. The deterministic implementations perform task work and report outputs or failures. |
| `scenarios/` | Assembles plans and deterministic handlers into the sequential S-01 -> S-02 -> S-03 demonstration and exports run evidence. |
| `app/` | The governed work product: a FastAPI URL shortener with SQLite persistence. |

An executor returns content and validation results; the engine assigns artifact
identity, version, hash, and lineage and decides whether that output may advance
the workflow. The seam and engine-owned output processing are exercised by
`tests/test_contracts.py::test_registered_handler_overrides_the_stub` and
`tests/test_engine.py::test_fork_join_executes_end_to_end_with_ordered_events`.

The only implemented executor path is deterministic and credential-free. No
model-backed executor was built.

## DAG execution and gates

`orchestrator/graph.py` validates task identifiers, dependencies, cycles,
artifact producers, and ancestry before execution. It computes a deterministic
topological order and readiness frontier. The graph behavior is checked by:

- `tests/test_graph.py::test_topological_frontier_and_descendants_are_deterministic`
- `tests/test_graph.py::test_cycle_and_unknown_dependency_are_rejected`
- `tests/test_graph.py::test_consumed_artifact_must_come_from_a_dependency_ancestor`

Entry gates decide whether declared inputs and other preconditions permit a task
to start. Exit gates decide whether its declared outputs are sufficient to count
as completed. Missing evaluators and missing evidence fail closed
(`tests/test_engine.py::test_declared_gate_without_evaluator_fails_closed` and
`tests/test_greenfield_scenario.py::test_s01_incomplete_requirement_fails_closed_at_entry`).

A fork exposes independent ready tasks. A join runs only after all dependencies
finish, every consumed artifact exists and is fresh, and each producer passes its
exit gates. This synchronization is checked by
`tests/test_engine.py::test_missing_branch_output_blocks_join`,
`tests/test_governance_negative.py::test_join_cannot_be_executed_while_a_branch_is_still_incomplete`,
and `tests/test_governance_negative.py::test_join_does_not_advance_when_a_branch_fails_its_exit_gate`.

## State machines

`orchestrator/contracts.py` is the frozen design contract. Its task machine
includes pending, ready, awaiting-approval, running, retrying, terminal outcomes,
and stale/re-planned work. Its run machine includes pending, running,
awaiting-approval, replanning, and the terminal `succeeded`, `failed`, and
`safe_stopped` outcomes.

The engine asks `is_legal_task_transition` and `is_legal_run_transition` before
moving state. Coverage of every task/run state and rejection of illegal moves is
provided by `tests/test_contracts.py::test_every_task_state_has_a_transition_entry`,
`tests/test_contracts.py::test_every_run_state_has_a_transition_entry`, and
`tests/test_engine.py::test_illegal_transition_is_rejected_without_an_event`.
Directly invoking an individually legal transition cannot bypass the governed
operation that earns it
(`tests/test_engine.py::test_individually_legal_transition_cannot_bypass_governed_operations`).

High-impact work pauses for an attributable human approval. Agent-authored or
mis-correlated approvals cannot release it
(`tests/test_governance_negative.py::test_engine_refuses_an_agent_granted_approval`,
`tests/test_governance_negative.py::test_an_approval_for_another_task_does_not_release_this_one`).
Human approval resumes the eligible task
(`tests/test_governance_negative.py::test_approval_from_a_human_control_is_load_bearing`).

Recovery is bounded by `Task.retry_budget`. Exhaustion executes a named
compensation when configured and then ends `safe_stopped`, never `succeeded`.
These claims are checked by
`tests/test_governance_negative.py::test_retry_attempts_are_bounded_by_the_declared_budget`,
`tests/test_governance_negative.py::test_compensation_runs_before_the_safe_stop`,
and `tests/test_governance_negative.py::test_retry_exhaustion_safe_stops_and_never_claims_success`.

## Event stream and projections

`orchestrator/events.py` is the append-only record. Each immutable event carries
the run id, an engine-assigned contiguous per-run sequence, injected timestamp,
actor, optional task id, summary, and payload. JSONL persistence appends rather
than rewriting previous bytes. Sequence enforcement, persistence, and protection
against nested payload mutation are checked by
`tests/test_events.py::test_store_assigns_monotonic_sequence_per_run`,
`tests/test_events.py::test_jsonl_store_rehydrates_without_rewriting`, and
`tests/test_events.py::test_nested_payload_mutation_cannot_rewrite_stored_history`.

The event stream is the common source for reviewer views:

- Metrics are a pure event projection through `compute_run_metrics`; equality
  with engine metrics is checked by
  `tests/test_governance_negative.py::test_metrics_are_reproducible_from_the_event_stream_alone`.
- Evidence exports the raw `events.jsonl` plus canonical projections for plans,
  graph, artifacts, decisions, controls, metrics, contexts, and the terminal
  result. Bundle membership and hashes are checked by
  `tests/test_scenario_runner.py::test_exporter_writes_indexed_reproducible_bundle`.
- Lineage is read through versioned contexts, superseding plans, derived-from
  artifact ids, stale markers, and their corresponding re-plan/invalidation
  events. Retention and selective invalidation are checked by
  `tests/test_ambiguous_scenario.py::test_s03_replan_preserves_v1_and_selectively_invalidates_analytics`
  and `tests/test_governance_negative.py::test_replan_appends_to_history_and_never_rewrites_it`.

Re-planning therefore adds a context and plan revision, marks only the changed
lineage and descendants stale, retains unrelated work, and requires fresh output
before downstream consumption. The load-bearing counterfactual is
`tests/test_governance_negative.py::test_selective_invalidation_control_is_load_bearing`;
stale consumption is rejected by
`tests/test_governance_negative.py::test_consumer_cannot_execute_while_its_input_is_invalidated`.
