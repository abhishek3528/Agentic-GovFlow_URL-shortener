# Scenario Acceptance Narratives

## Common evidence contract

Every scenario run must emit a stable run identifier and an evidence bundle containing:

- Original and normalized requirement versions, ambiguities, assumptions, and acceptance checks.
- Task dependency graph, task input/output contracts, owners or capability roles, and gate definitions.
- Persisted terminal state plus correlated transition, policy, approval, retry, compensation, validation, and artifact events.
- Decision lineage from requirement through generated or changed artifact and validation result.
- Scenario result, limitations encountered, event-derived reliability metrics, and links or paths to reviewable outputs.

Evidence must be reproducible from a documented command and must not require a live external model.

## S-01: Greenfield core service

### Narrative

Given a well-defined request to establish the URL-shortener baseline, the workflow normalizes the request, decomposes it across the SDLC, and builds a runnable core slice. After the normalized requirement and design intent pass their gates, implementation work forks into at least two independent paths, such as core redirect/create behavior and analytics/reliability work. Their outputs join before integrated validation, documentation, and a human-owned release-readiness gate.

### Required orchestration behavior

- Entry validation rejects an incomplete or structurally invalid request.
- The task DAG visibly spans requirement, design, implementation, unit/integration validation, documentation, and release readiness.
- At least one sequential dependency and one parallel fork/join are observable in event ordering.
- The join cannot advance until all required branch outputs exist and pass their exit checks.
- A policy check rejects an intentionally unsafe URL fixture while permitted work continues correctly.
- A transient task failure is injected; the task succeeds within a bounded retry budget and the retry is reflected in metrics.
- Final release readiness remains human-owned and cannot be reached by an agent-only transition.

### Product outcome

The established baseline creates and redirects short URLs, returns basic analytics, exposes health/readiness, publishes its contract, and passes product and workflow-control tests.

### Acceptance evidence

- Runnable smoke path for create -> redirect -> analytics.
- API contract plus unit/integration results for normal, invalid, collision/idempotency, not-found, and health behavior.
- DAG and event trace proving fork/join and ordered lifecycle gates.
- Failed unsafe-input policy record and a successful bounded-retry trace.
- Approval/release-readiness record and event-derived run metrics.

## S-02: Brownfield reliability change

### Narrative

Given the S-01 baseline and a change request to correct a reliability defect or make a bounded reliability enhancement, the workflow inspects repository evidence before planning the change. It produces an impact analysis naming affected modules, API behavior, persistence/data flow, tests, and documentation. A failure-injection fixture reproduces the prior behavior, then a gated change supplies before/after proof.

The default behavioral target is a collision/idempotency correctness change because it naturally crosses API, domain, persistence, test, and documentation boundaries without expanding product scope. Phase 02 may choose an equivalent bounded reliability target if it proves the same acceptance properties and records the rationale.

### Required orchestration behavior

- Planning is blocked until repository-grounded impact analysis and a regression reproduction exist.
- The graph links the changed requirement to affected components, tests, contract/docs, and release checks.
- Test and documentation work can fork after the implementation/change contract is stable, then synchronize before final validation.
- The selected change is classified for impact. Any breaking contract or destructive migration alternative requires human approval; a bypass attempt must fail.
- One injected persistent failure exhausts its retry budget, invokes an explicit fallback or compensating state restoration, and ends safely without claiming success.
- A subsequent corrected run resumes or restarts according to policy and produces a valid terminal result without erasing the failed run.

### Product outcome

The reliability defect is demonstrated before the change and eliminated afterward without regressing create, redirect, analytics, or health behavior. Impacted documentation and contract material remain consistent.

### Acceptance evidence

- Repository citations or paths in the impact analysis, including module/API/data-flow/test/doc effects.
- Red test or failure fixture before the change and green focused plus regression results after it.
- Approval classification, blocked bypass transition, bounded-retry exhaustion, compensation/fallback, and safe-stop trace.
- Before/after artifact lineage, synchronized branch evidence, and retry/rollback/MTTR metric calculations.

## S-03: Ambiguous analytics/privacy change with governed re-plan

### Narrative

Given an intentionally ambiguous request such as “improve link analytics,” the workflow identifies missing dimensions, privacy/retention constraints, acceptance thresholds, and scope boundaries instead of silently choosing an implementation. It proposes a minimal assumption set and pauses at a human clarification/approval checkpoint. Work begins from the approved normalized version.

An upstream clarification then changes a material analytics/privacy assumption—for example, disallowing storage of raw client identifiers and requiring only a coarse derived dimension. The workflow versions the requirement, computes affected descendants, marks only those plans/artifacts stale, preserves unaffected outputs, recomputes the dependency subgraph, and re-applies policy, validation, and approval gates.

### Required orchestration behavior

- Ambiguities are classified and exposed with proposed assumptions and consequences.
- Execution cannot pass the normalization gate until assumptions are approved or explicitly bounded by policy.
- Analytics design and documentation/test planning demonstrate parallelizable work with a gated join.
- The changed upstream requirement produces a new context version and an attributable decision.
- Affected descendant tasks and artifacts are invalidated; unaffected work is visibly retained.
- The revised plan passes security/privacy and change-control gates; stale artifacts cannot be consumed.
- Final quality approval references the revised requirement version and complete validation evidence.

### Product outcome

The existing service gains only the approved, privacy-conscious analytics behavior. The final contract, tests, and documentation match the revised requirement and state explicit retention/precision limitations.

### Acceptance evidence

- Initial ambiguity report, assumption proposal, clarification/approval record, and normalized requirement v1.
- Requirement v2 with change reason, affected-node calculation, stale markings, revised DAG, and preserved unaffected lineage.
- Failed stale-artifact transition test plus re-applied policy/gate results.
- Product tests for the revised analytics behavior and negative checks that prohibited raw data is not retained.
- Final event trace and metrics spanning the re-plan without overwriting v1 history.

## Cross-scenario acceptance matrix

| Capability | S-01 | S-02 | S-03 |
|---|---|---|---|
| Well-defined normalization and decomposition | Primary | Reinforced | Contrasted with ambiguity |
| Codebase impact analysis | Baseline references | Primary | Changed-artifact impact |
| Sequential and fork/join execution | Primary | Reinforced | Reinforced |
| Human approval and bypass prevention | Release gate | High-impact change | Assumptions and final quality |
| Policy guardrails | Unsafe destination | Change classification | Privacy and stale-input policy |
| Retry | Transient recovery | Exhaustion path | Metrics include any re-execution |
| Fallback/compensation/safe-stop | Bounded recovery | Primary failure path | Stale work prevented |
| Decision/context lineage | End-to-end | Before/after | Primary versioned re-plan proof |
| Event-derived metrics | Baseline run | Recovery/MTTR proof | Re-plan latency/run proof |
| Production-shaped outputs | Core service | Safe brownfield change | Consistent analytics change |
