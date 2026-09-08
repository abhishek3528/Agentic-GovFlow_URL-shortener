# Requirements Traceability

## Requirement baseline

| ID | Requirement | Source | Priority | Planned evidence | Status |
|---|---|---|---|---|---|
| REQ-001 | Transform a requirement into a reviewable engineering outcome with an agentic execution model. | p.1, Objective | P0 | End-to-end scenario runs and reviewable artifact bundle | Baseline |
| REQ-002 | Demonstrate requirement understanding, task decomposition, multi-step execution, and output generation/validation. | p.1, Objective | P0 | Scenario inputs, normalized requirements, graphs, execution traces, validated outputs | Baseline |
| REQ-003 | Focus on end-to-end SDLC automation with controlled autonomy. | p.1, Objective | P0 | Lifecycle graph, autonomy policy, approvals and terminal quality gate | Baseline |
| REQ-004 | Build a URL-shortener service from scratch with core APIs, analytics, and reliability features. | p.1, Scenario | P0 | Runnable service, API contract, analytics tests, reliability tests | Baseline |
| REQ-005 | Complete and improve the solution over 2-3 days using AI assistance while demonstrating engineering judgment. | p.1, Scenario | P0 | Prioritized scope, decision log, agentic execution evidence, limitations | Baseline |
| REQ-006 | Cover greenfield engineering work. | p.1, Scope | P0 | Greenfield scenario | Baseline |
| REQ-007 | Cover brownfield enhancements, refactors, or bug fixes. | p.1, Scope | P0 | Brownfield scenario with impact analysis and before/after evidence | Baseline |
| REQ-008 | Cover test and documentation improvements. | p.1, Scope | P0 | Scenario tasks and diffs for tests/docs | Baseline |
| REQ-009 | Handle both well-defined and ambiguous requirements. | p.1, Scope | P0 | Normal requirement and ambiguity-resolution/re-plan traces | Baseline |
| REQ-010 | Interpret intent, identify ambiguity, and normalize a clear engineering problem. | p.1, Core Requirement 1 | P0 | Structured intake output and ambiguity/assumption records | Baseline |
| REQ-011 | Decompose high-level requirements into actionable tasks with dependencies and sequencing. | p.1, Core Requirement 2 | P0 | Explicit task graph with dependency validation | Baseline |
| REQ-012 | In brownfield work, identify impacted modules, services, APIs, and data flows and demonstrate architectural understanding. | p.1, Core Requirement 3 | P0 | Impact-analysis artifact tied to repository evidence | Baseline |
| REQ-013 | Orchestrate requirements, architecture/design, implementation, testing, documentation, and release readiness. | pp.1-2, Core Requirement 4 | P0 | Executable lifecycle graph and stage artifacts | Baseline |
| REQ-014 | Demonstrate non-linear, stateful execution with governance rather than linear task chaining. | pp.1-2, Core Requirement 4 | P0 | Persisted workflow state, conditional branches, transition-policy tests | Baseline |
| REQ-015 | Use an explicit dependency graph with entry and exit gates. | pp.1-2, Core Requirement 4 | P0 | Graph definition, gate results, invalid-transition tests | Baseline |
| REQ-016 | Support sequential and parallel paths with synchronization. | p.2, Core Requirement 4 | P0 | Fork/join scenario and event timing/order evidence | Baseline |
| REQ-017 | Preserve cross-stage context and decision lineage. | p.2, Core Requirement 4 | P0 | Versioned context, correlated events, decision records | Baseline |
| REQ-018 | Enforce human approval checkpoints for high-impact actions. | p.2, Core Requirement 4 | P0 | Paused workflow, approval record, bypass-prevention tests | Baseline |
| REQ-019 | Include bounded retries, fallback, rollback, and safe-stop controls. | p.2, Core Requirement 4 | P0 | Failure-injection runs and recovery/terminal-state tests | Baseline |
| REQ-020 | Embed policy guardrails for security, compliance, and change control. | p.2, Core Requirement 4 | P0 | Policy definitions, allow/deny evidence, audit trail | Baseline |
| REQ-021 | Provide audit-grade observability and traceability. | p.2, Core Requirement 4 | P0 | Append-only/correlated event history and requirement-to-artifact trace view | Baseline |
| REQ-022 | Track success rate, retry/rollback frequency, MTTR, and end-to-end latency. | p.2, Core Requirement 4 | P0 | Metrics derived from execution events and calculation tests | Baseline |
| REQ-023 | Dynamically re-plan when upstream outputs change while maintaining governance and controlled autonomy. | p.2, Core Requirement 4 | P0 | Change-triggered invalidation/re-plan trace with gates re-applied | Baseline |
| REQ-024 | Produce production-quality code, API/schema definitions, unit/integration tests, and supporting documentation with clean, maintainable design. | p.2, Core Requirement 5 | P0 | Source, API schema, test suite, docs, static/quality checks | Baseline |
| REQ-025 | Identify risks, trade-offs, and failure scenarios and define validation and safety guardrails. | p.2, Core Requirement 6 | P0 | Risk register, threat/failure model, validation plan and negative tests | Baseline |
| REQ-026 | Let agents execute multi-step work while humans provide oversight, approvals, and final quality control. | p.2, Core Requirement 7 | P0 | Autonomy policy, approval transitions, final human gate | Baseline |
| REQ-027 | Include plan/rationale, artifacts, risks/trade-offs/validation, assumptions, and limitations in the final engineering summary. | p.2, Core Requirement 8 | P0 | Final engineering summary checklist | Baseline |
| REQ-028 | Treat the work as production-grade, showing strong design fundamentals, lifecycle orchestration, output ownership, and defensible reasoning. | p.3, Expectation | P0 | Architecture/decision records, ownership and validation evidence | Baseline |
| REQ-029 | Keep agents within defined autonomy boundaries and humans responsible for oversight, approvals, and final quality. | p.3, Expectation | P0 | Policy model and explicit human-owned gates | Baseline |

## Deliverable baseline

| ID | Deliverable | Source | Priority | Planned evidence | Status |
|---|---|---|---|---|---|
| DEL-001 | Working prototype runnable end-to-end. | p.2, Deliverables | P0 | Clean setup and end-to-end smoke run | Baseline |
| DEL-002 | Architecture overview covering components, orchestration model, control flow, and key decisions. | p.2, Deliverables | P0 | Architecture document and decision records | Baseline |
| DEL-003 | Greenfield, brownfield, and ambiguous scenarios, each showing decomposition, orchestration, and validation. | p.2, Deliverables | P0 | Three repeatable scenario commands and evidence bundles | Baseline |
| DEL-004 | Setup instructions. | p.2, Deliverables | P0 | Reviewer quickstart validated from a clean state | Baseline |
| DEL-005 | Testing approach, limitations, and trade-offs. | p.2, Deliverables | P0 | Test strategy and final summary sections | Baseline |
| DEL-006 | Final engineering summary. | p.2, Core Requirement 8 | P0 | Submission summary with all named content | Baseline |

## Evaluation-signal baseline

| ID | Evaluation signal | Source | Priority | Planned evidence | Status |
|---|---|---|---|---|---|
| EVAL-001 | Effectiveness of agentic orchestration. | p.2, Evaluation Criteria | P0 | Stateful graph and controlled execution/recovery/re-plan traces | Baseline |
| EVAL-002 | Architecture/system design quality. | p.2, Evaluation Criteria | P0 | Architecture views, clear boundaries, ADRs | Baseline |
| EVAL-003 | Depth of decomposition and execution quality. | p.2, Evaluation Criteria | P0 | Scenario graphs, dependencies, outputs, validation | Baseline |
| EVAL-004 | Realism and quality of outputs. | p.2, Evaluation Criteria | P0 | Runnable service and production-shaped artifacts | Baseline |
| EVAL-005 | Validation and risk-management rigor. | p.2, Evaluation Criteria | P0 | Automated positive/negative tests, risk/failure controls | Baseline |
| EVAL-006 | Clarity and defensibility of decisions. | p.2, Evaluation Criteria | P0 | Decision lineage and concise rationale/trade-offs | Baseline |
| EVAL-007 | Modular, testable, reliable, secure, scalable code with safe change management. | p.2, Evaluation Criteria | P0 | Quality checks, security controls, scaling rationale, gated change scenario | Baseline |
| EVAL-008 | Engineering judgment. | p.2, Evaluation Criteria | P0 | Scope discipline, risk decisions, transparent limitations | Baseline |

## Phase 01 accepted coverage map

The baseline rows above preserve the source extraction. The following map is the accepted Phase 01 scope and supplies a concrete component, scenario, check, and artifact strategy for every item. `S-01`, `S-02`, and `S-03` refer to the scenario narratives in `plan/phases/01-acceptance-strategy/SCENARIO_ACCEPTANCE.md`.

### Requirements

| ID | Planned scope/component | Scenario allocation | Planned check and artifact | Phase 01 status |
|---|---|---|---|---|
| REQ-001 | Scenario runner and evidence exporter connect requirement intake to reviewable product outputs. | S-01 primary; all | Replay each scenario and inspect its indexed evidence bundle plus terminal result. | Accepted scope |
| REQ-002 | Normalizer, task DAG, role/capability execution, artifact registry, and validation gates. | All | Assert normalized input, dependent tasks, execution events, outputs, and validation results exist per run. | Accepted scope |
| REQ-003 | Lifecycle workflow with autonomy policy and human-owned terminal quality gate. | All | Reject out-of-policy actor transitions and agent-only release; retain approval evidence. | Accepted scope |
| REQ-004 | Minimal product: create, redirect, analytics, health/readiness, and bounded reliability behaviors. | S-01 primary; regressions in S-02/S-03 | Product smoke, API contract, unit/integration, invalid-input, idempotency/collision, and health checks. | Accepted scope |
| REQ-005 | Scope freeze, decision log, three evidence-rich scenarios, explicit limitations. | All | Final audit confirms all P0 evidence and no optional feature blocks the critical path. | Accepted scope |
| REQ-006 | Greenfield baseline workflow and product vertical slice. | S-01 | Run from requirement through approved release-readiness state and verify generated baseline artifacts. | Accepted scope |
| REQ-007 | Repository-grounded reliability change against the established baseline. | S-02 | Verify impact analysis, red-to-green fixture, before/after lineage, and full regression. | Accepted scope |
| REQ-008 | Test and documentation tasks are first-class DAG nodes with output contracts. | All; primary S-02/S-03 | Block joins/final gate when required test or documentation output is missing or stale. | Accepted scope |
| REQ-009 | Normal intake in S-01/S-02 and fail-closed ambiguity handling plus re-plan in S-03. | All | Compare approved normalized inputs; verify ambiguity blocks progress until resolved. | Accepted scope |
| REQ-010 | Versioned requirement normalizer, ambiguity register, assumptions, and acceptance checks. | S-03 primary; all | Test incomplete intake, material-ambiguity pause, attributable approval, and v1/v2 records. | Accepted scope |
| REQ-011 | Explicit validated task DAG with named inputs, outputs, dependencies, roles, and gates. | All | Validate acyclicity/dependencies; reject task start before prerequisites; export graph. | Accepted scope |
| REQ-012 | Impact-analysis task consuming repository evidence and naming modules, APIs, data flow, tests, and docs. | S-02 primary; S-03 affected-artifact analysis | Gate planning on cited impact analysis; inspect repository paths and affected-node links. | Accepted scope |
| REQ-013 | Lifecycle nodes for requirements, design, implementation, testing, docs, and release readiness. | All | Graph completeness check and stage artifact/gate assertions in each run. | Accepted scope |
| REQ-014 | Persisted workflow/task state, conditional transitions, and non-linear execution policy. | All | Transition-table positive/negative tests plus pause/restart/inspect evidence. | Accepted scope |
| REQ-015 | Dependency graph with explicit per-node and lifecycle entry/exit gates. | All | Dependency/gate validation and invalid-transition tests; export gate results. | Accepted scope |
| REQ-016 | Parallel implementation/test-doc paths and enforced synchronization join. | All; primary S-01 | Event-order proof plus incomplete-join rejection and successful aggregate gate. | Accepted scope |
| REQ-017 | Versioned context, decisions, artifact lineage, and correlated append-only events. | All; primary S-03 | Trace any final artifact back to requirement version and decision; confirm prior versions remain. | Accepted scope |
| REQ-018 | Human approval states for assumptions, high-impact changes, policy exceptions, and release quality. | S-01 release; S-02 impact; S-03 assumptions/final | Paused-state, actor-attribution, approve/reject, and bypass-prevention tests. | Accepted scope |
| REQ-019 | Retry budgets, retryability, fallback/compensation, state restoration, and safe-stop. | S-01 transient; S-02 exhaustion | Inject transient and persistent failures; assert attempt cap, compensation/fallback, terminal state, history. | Accepted scope |
| REQ-020 | Generic policy engine for URL/input safety, secret/PII, privacy, provenance, evidence retention, role boundaries, and change control. | All | Named allow/deny fixtures and persisted policy decisions; no formal compliance claim. | Accepted scope |
| REQ-021 | Correlated append-only event history and requirement-to-artifact trace view/export. | All | Reconstruct scenario chronology and lineage; assert events cannot be silently replaced through normal interfaces. | Accepted scope |
| REQ-022 | Metrics projection from events: success rate, retry/rollback frequency, MTTR, and end-to-end latency. | All | Calculation tests against deterministic event fixtures and reconciliation with scenario histories. | Accepted scope |
| REQ-023 | Context versioning, dependency-based selective invalidation, graph recomputation, and freshness gates. | S-03 primary | Change approved requirement; assert affected stale/unaffected retained, stale use denied, gates repeated. | Accepted scope |
| REQ-024 | Modular source, machine-readable API contract, unit/integration tests, quality checks, and docs. | S-01 baseline; S-02/S-03 changes | Clean quality suite and scenario-specific artifact/regression validation. | Accepted scope |
| REQ-025 | Phase risk register, scenario failure paths, policy controls, validation plan, and negative tests. | All | Map each critical/high risk to prevention plus executable or review evidence; audit residuals. | Accepted scope |
| REQ-026 | Capability-bounded role execution with human assumption/change/release gates. | All | Actor-policy tests, approval records, and final human quality decision. | Accepted scope |
| REQ-027 | Final summary checklist for rationale, artifacts, risks/trade-offs/validation, assumptions, and limitations. | Cross-scenario submission | Automated/manual completeness gate against all named sections and evidence links. | Accepted scope |
| REQ-028 | Production-shaped vertical slice and explicit architecture/decision/ownership/validation evidence. | All | Architecture and decision review plus clean setup, tests, security controls, and limitation audit. | Accepted scope |
| REQ-029 | Central autonomy policy and fail-closed human-owned checkpoints. | All | Negative actor/transition tests and attributable approval/final-quality records. | Accepted scope |

### Deliverables

| ID | Planned scope/component | Scenario allocation | Planned check and artifact | Phase 01 status |
|---|---|---|---|---|
| DEL-001 | Credential-free local prototype with short setup and scenario runner. | All | Clean-state setup and end-to-end smoke for product plus all scenario runs. | Accepted scope |
| DEL-002 | Architecture overview of product, orchestration, state/control flow, policies, evidence, and decisions. | Cross-scenario | Phase 02 architecture review checklist and linked decision records. | Accepted scope |
| DEL-003 | Three named repeatable evidence bundles against one evolving codebase. | S-01, S-02, S-03 | One documented command and complete common evidence contract per scenario. | Accepted scope |
| DEL-004 | Reviewer quickstart covering prerequisites, setup, tests, scenarios, approvals, and evidence inspection. | Cross-scenario | Validate from a clean state without mandatory cloud credentials. | Accepted scope |
| DEL-005 | Test strategy plus explicit prototype limitations and trade-offs. | Cross-scenario | Confirm positive, negative, failure, restart, metric, and scenario layers are covered and limitations are candid. | Accepted scope |
| DEL-006 | Final engineering summary with all required named content and trace links. | Cross-scenario | Submission completeness checklist and final evaluator audit. | Accepted scope |

### Evaluation signals

| ID | Planned scope/component | Scenario allocation | Planned check and artifact | Phase 01 status |
|---|---|---|---|---|
| EVAL-001 | Policy-enforced stateful lifecycle graph with recovery and re-plan. | All | Replay traces and transition-control tests demonstrate behavior rather than labels. | Accepted scope |
| EVAL-002 | Explicit component boundaries, control/data flow, contracts, and decision records. | Cross-scenario | Architecture quality review against requirements, risks, and feasibility. | Accepted scope |
| EVAL-003 | Granular DAG tasks with dependencies, roles, output contracts, gates, and synchronized paths. | All | Graph validator, event histories, and artifact completeness per scenario. | Accepted scope |
| EVAL-004 | Runnable URL-shortener behavior and production-shaped contract/tests/docs. | All | Clean setup, smoke/regression runs, and reviewer-visible artifact bundles. | Accepted scope |
| EVAL-005 | Risk-linked validation, policy negatives, failure injection, retry/compensation/safe-stop, and stale prevention. | All | Automated control/failure suites and risk-to-evidence audit. | Accepted scope |
| EVAL-006 | Versioned assumptions, approvals, decisions, rationale, and artifact lineage. | All; primary S-03 | Trace queries and decision review reconstruct why each material action occurred. | Accepted scope |
| EVAL-007 | Modular design, contract tests, security guards, deterministic reliability checks, scale rationale, and gated changes. | All | Quality/security/regression suites plus architecture evolution narrative. | Accepted scope |
| EVAL-008 | Evidence-first scope discipline, explicit non-goals, residual risks, trade-offs, and limitations. | Cross-scenario | Phase gates and final audit show P0 completeness before optional polish. | Accepted scope |

## Status meaning

`Accepted scope` means Phase 01 has assigned the item to an observable product or orchestration component, scenario, check, and artifact. Phase 02 must translate these behavioral allocations into a feasible design. Later phases replace the effective status with planned, implemented, verified, or deferred-with-rationale; the original baseline extraction remains intact for auditability.
