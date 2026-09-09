# Architecture

## Governing idea

The repository separates decisions about work from the work itself:

| Area | Responsibility |
|---|---|
| `orchestrator/` | Owns plans, legal state transitions, readiness, gates, policies, approvals, artifact provenance, recovery, re-planning, and events. |
| `orchestrator/executor.py` | Defines the narrow `TaskExecutor.execute(task, inputs) -> TaskOutput` work seam. The deterministic implementations perform task work and report outputs or failures. |
| `orchestrator/agents.py` | Implements named role agents and two-level capability -> task-handler dispatch, failing closed on missing ownership. |
| `orchestrator/planner.py` | Derives a `Plan` from a requirement's text. One unconditional path, no requirement-specific branches. |
| `scenarios/` | Assembles plans and deterministic agent handlers into the sequential S-01 -> S-02 -> S-03 demonstration and exports run evidence. |
| `app/` | The governed work product: a FastAPI URL shortener with SQLite persistence. |

An executor returns content and validation results; the engine assigns artifact
identity, version, hash, and lineage and decides whether that output may advance
the workflow. The seam and engine-owned output processing are exercised by
`tests/test_contracts.py::test_registered_handler_overrides_the_stub` and
`tests/test_engine.py::test_fork_join_executes_end_to_end_with_ordered_events`.

Two `TaskExecutor` paths are implemented and credential-free.
`DeterministicExecutor` remains the direct handler/stub path used by core and
negative tests. The scenarios use `AgentRegistry`: `Task.capability` first
selects the owning `Agent`, then the task id selects that agent's handler. A
missing capability or handler raises rather than stubbing, and execution events
are attributed to the resolved agent. These claims are exercised by
`tests/test_agents.py`. No model-backed executor was built.

## What "agentic" means here

The word carries two meanings, and this project satisfies one of them
deliberately rather than the other by accident.

The assessment defines it in its closing principle: *"Agents execute under
defined autonomy boundaries; humans own oversight, approvals, and final
quality."* That is a statement about **autonomy and governance** — who is
allowed to do what, and where a person must intervene. It is the definition this
system is built to.

The popular meaning is narrower: an agent is a large language model in a loop.
No model is invoked anywhere in this repository, so by that reading it would not
qualify. The distinction is worth stating plainly rather than leaving a reviewer
to infer it.

**What is actually here:**

- **Named agents that own work.** Fourteen roles — `backend-engineer`,
  `quality-engineer`, `privacy-owner` and the rest — registered in an
  `AgentRegistry`. A task declares a `capability`; that capability selects the
  agent; the agent selects its handler. A capability no agent owns raises
  `AgentDispatchError` rather than running, so the role is an executable
  contract and not a label
  (`tests/test_governance_negative.py::test_an_unknown_capability_fails_closed`).
- **Attribution in the audit trail.** Every event names the agent that acted —
  `agent:quality-engineer`, `agent:release-manager` — and the boundary between
  agent and human is enforced, not annotated. An agent named to look like a
  person still records as `AGENT` and still cannot grant an approval
  (`::test_an_agent_cannot_attribute_its_work_to_a_human`).
- **Autonomy with bounds.** Agents run multi-step work unattended; gates,
  policies, bounded recovery and human approval decide how far that autonomy
  extends.

**Why execution is deterministic.** It is a trade, not an omission. A
credential-free executor means a reviewer runs the whole system offline with no
API key, and two runs produce byte-identical evidence — which is what makes
every governance claim in these documents checkable rather than assertable. A
model-backed executor would forfeit both.

**What that costs.** The narrow reading goes undemonstrated: nothing here proves
the control plane survives contact with a non-deterministic worker. The
mitigation is structural rather than rhetorical — the `TaskExecutor` seam
already carries two independent implementations (`DeterministicExecutor` and
`AgentRegistry`), so a third that calls a model would slot in without touching
gates, approvals, recovery, or audit. That is the strongest available evidence
short of building it, and it is stated as a limitation in
`docs/FINAL_SUMMARY.md` rather than glossed.

## Where the plan comes from

`orchestrator/planner.py` derives a `Plan` from a `ContextVersion`.
Decomposition is a governance decision, so it belongs in the control plane
rather than in the scenarios that consume it. Signals detected in the
requirement text add or drop nodes, so plan size tracks risk: a documentation
change derives three tasks, a persistence change seven with a `data-design`
node, a defect fix eight with impact analysis and a red reproduction ahead of
planning, and a requirement combining a regression, a migration and a breaking
contract change eleven. `govflow plan "<requirement>"` prints the result.

Every derived plan is validated by the same `DependencyGraph` that validates a
hand-written one — the identical rejection rules, with no special path for
generated input. Ordering never depends on set iteration, because Python
randomises string hashing per process and a set-driven task order would differ
between runs; `tests/test_planner.py` asserts identical output across processes
started with different `PYTHONHASHSEED` values.

A deriver answers the meaning, not the string. An earlier iteration passed its
acceptance check by recognising one requirement's exact wording and returning a
stored plan; appending a single word changed the output entirely. That was a
lookup table and was removed.
`tests/test_planner.py::test_a_paraphrased_requirement_derives_a_comparable_plan`
now holds the line the original check failed to.

The three scenarios keep hand-built task graphs rather than derived ones. Theirs
exist to exercise particular controls — two implementation branches meeting at a
join, a red reproduction gating planning, a re-plan invalidating exactly five of
nineteen artifacts — which a generic planner does not produce, and reproducing
them would have required one recogniser per scenario. Derivation is therefore a
demonstrated capability rather than the execution path, and
`docs/FINAL_SUMMARY.md` records that as a limitation rather than glossing it.

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
