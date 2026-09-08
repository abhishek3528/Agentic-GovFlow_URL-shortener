# Phase 01 Acceptance Strategy

## Strategy statement

Win evaluator confidence by delivering the smallest coherent URL-shortener product that can serve as credible evidence for a governed, stateful software-engineering workflow. Product functionality establishes realism; the primary differentiator is an inspectable orchestration lifecycle whose claims are backed by repeatable scenarios, tests, persisted traces, and candid limitations.

The strategy is evidence-first: no important capability is considered demonstrated by documentation alone when it can be exercised or tested.

## Ranked evaluator goals

| Rank | Evaluator goal | Confidence question | Required observable evidence | Failure signal |
|---|---|---|---|---|
| 1 | Governed agentic orchestration | Is this a non-linear, stateful engineering system rather than a scripted chain? | Persisted dependency graph and workflow state; entry/exit gates; fork/join; conditional transitions; approval pause; bounded recovery; policy decisions; re-plan trace | A fixed happy-path sequence, labels without enforced transitions, or screenshots without replayable runs |
| 2 | Validation and risk control | Are unsafe, invalid, or failed actions detected and contained? | Negative and failure-injection tests; retry budget; fallback or compensation; safe-stop; blocked policy and approval transitions; terminal quality gate | Unbounded retries, cosmetic approval, or unsupported safety claims |
| 3 | Decomposition and execution depth | Can the system turn intent into realistic, dependent engineering work? | Normalized requirement, task DAG, owners/capabilities, dependencies, artifacts, validation results, and synchronized parallel work | Flat checklist with no dependencies or output contracts |
| 4 | Real engineering output | Does the system produce a useful, runnable outcome? | Working core URL-shortener APIs, analytics, reliability behavior, contract, tests, docs, and clean quickstart | Demo-only stubs, hand-edited evidence, or unreproducible setup |
| 5 | Decision clarity and auditability | Can a reviewer reconstruct what happened and why? | Correlated append-only events, versioned context, assumptions, approvals, decisions, artifact links, and requirement trace view | Logs without causality, overwritten state, or unexplained decisions |
| 6 | Architecture and core engineering quality | Are boundaries maintainable and choices defensible? | Architecture views, decision records, modular contracts, security controls, quality checks, and credible scale evolution | Premature infrastructure, hidden coupling, or stack-driven justification |
| 7 | Controlled adaptability | Does upstream change trigger correct re-planning without bypassing governance? | Changed requirement version, descendant invalidation, recomputed plan, re-applied gates, and preserved prior history | Mutating the plan in place or continuing with stale outputs |
| 8 | Engineering judgment | Is the 2-3 day scope deliberate, complete, and honest? | Prioritized scope, explicit non-goals, trade-offs, risk register, limitations, and evidence-backed claims | Broad polish with missing required proof or implied production claims |

## Minimum winning scope

### Product slice

- Create a short URL from a validated destination URL and return a stable short identifier.
- Resolve a short identifier through an HTTP redirect.
- Retrieve basic analytics: aggregate click count plus a deliberately small, privacy-conscious event view sufficient to prove measurement.
- Expose liveness/readiness behavior.
- Demonstrate deterministic collision handling, idempotent creation semantics, invalid/unsafe URL rejection, and predictable not-found behavior.
- Publish a machine-readable API contract, automated unit/integration tests, concise setup and usage documentation, and explicit prototype reliability assumptions.

### Orchestration slice

- Accept a requirement, identify ambiguity, and produce a versioned normalized engineering problem.
- Generate an explicit dependency DAG spanning requirements, design, implementation, testing, documentation, and release readiness.
- Persist workflow state, task state, artifacts, decisions, approvals, and correlated append-only execution events.
- Enforce entry/exit gates and reject invalid transitions.
- Execute both sequential and parallel task paths, with a join that validates required branch outputs.
- Pause high-impact work for an attributable human approval and prove bypass prevention.
- Apply named security, privacy, evidence-retention, and change-control policy checks with allow/deny records.
- Bound retry attempts, record backoff intent, select an explicit fallback or compensating action where applicable, and terminate in a safe stopped state when recovery is exhausted.
- Derive success rate, retry and rollback frequency, MTTR, and end-to-end latency from execution events.
- Respond to a versioned upstream requirement change by marking affected descendants stale, recomputing the plan, and re-applying gates while retaining the original lineage.
- Finish at a human-owned release-readiness/quality gate.

### Submission and repeatability slice

- One-command or equivalently short local setup with no mandatory cloud credentials.
- Three named, replayable scenario commands using one evolving codebase.
- Deterministic default execution; optional model integration may enrich behavior but cannot be required for evaluation.
- Evidence bundles generated by runs rather than manually fabricated: normalized inputs, DAGs, decisions, state/event traces, validation results, metrics, and artifact references.
- Architecture overview, test approach, trade-offs, assumptions, limitations, and final engineering summary.

## High-leverage differentiators

1. **Policy-enforced orchestration, not orchestration theater.** Invalid transitions, approval bypass, stale-artifact use, and policy violations are executable negative tests.
2. **Causal re-planning.** The ambiguous scenario shows a changed upstream requirement invalidating only affected descendants, preserving history, rebuilding the dependency subgraph, and repeating relevant gates.
3. **Evidence from a unified event model.** Audit history, decision lineage, scenario traces, and reliability metrics are different views over persisted execution events rather than separate sample data.
4. **A credible evolving-codebase story.** Greenfield establishes the baseline; brownfield first produces repository-grounded impact analysis, then changes behavior with before/after proof; ambiguous work evolves that same system.

These differentiators are mandatory within the winning scope, not stretch product features.

## Acceptance principles

- A capability claim needs at least one observable artifact and, for control behavior, at least one executable positive or negative check.
- Scenario success requires both a usable engineering output and a valid governed workflow terminal state.
- Required gates are fail-closed: missing evidence, approval, policy results, or synchronized branch outputs blocks advancement.
- Every task consumes named, versioned inputs and emits named outputs so lineage can be reconstructed.
- Re-planning never destroys prior state; superseded plans and decisions remain reviewable.
- Metrics are calculated from persisted events and tested against known event sequences.
- “Production-grade” describes engineering discipline and production-shaped artifacts, not a claim of production deployment or formal regulatory compliance.

## Explicit non-goals

- A visual workflow editor, or any graphical interface to the orchestration
  engine. (A small optional React client for the URL-shortener API was added
  under `web/` after the assessed scope was met. It consumes the existing
  endpoints, adds no product capability, and is explicitly outside the graded
  deliverable — the engine remains driven by its CLI and its evidence bundles.)
- Mandatory external LLM, SaaS, cloud, or enterprise credentials.
- Distributed execution, multi-region deployment, autoscaling infrastructure, or a production SLO claim.
- Formal compliance certification or claims specific to any organization's internal policy.
- Full identity platform, tenant administration, billing, marketing pages, or operational dashboards.
- Custom aliases, bulk shortening, link expiration, QR codes, rich analytics, geolocation, device fingerprinting, or long-term analytics retention.
- A comprehensive abuse-detection platform; only bounded URL/input safety controls needed for the prototype.
- Automatic source-control, database, or deployment rollback beyond demonstrated workflow compensation/state restoration; broader rollback boundaries will be documented.
- Multiple independent applications for the three scenarios.
- Polished features that do not strengthen an explicit requirement, risk mitigation, or acceptance signal.

## Feasibility guardrails for 2-3 days

- Use one product vertical slice and one orchestration engine across all scenarios.
- Prefer local durable persistence and deterministic fixtures over infrastructure breadth.
- Reuse a common scenario runner, event model, policy model, and evidence exporter.
- Implement the happy path only after control contracts and acceptance checks are defined; preserve time for negative paths and documentation.
- Treat clean setup, scenario replay, and evidence verification as P0 work, not end-of-project polish.

## Phase boundary

This strategy defines behavioral acceptance and evidence expectations only. Phase 02 must choose and justify the architecture, technology stack, exact API/schema contracts, persistence mechanisms, and executable graph design.
