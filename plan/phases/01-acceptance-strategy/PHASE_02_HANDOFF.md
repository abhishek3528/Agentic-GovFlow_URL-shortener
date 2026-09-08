# Handoff: Phase 02 - Solution Design

Read `plan/SOT.md` first and treat it as authoritative.

## Objective

Translate the accepted behavioral scope and evidence contracts into the smallest feasible architecture and technology design that can be implemented and verified within the 2-3 day constraint.

## Authorized scope

- Select and justify the product and orchestration technology stack.
- Define component/module boundaries, control flow, data flow, and trust boundaries.
- Define the minimal URL-shortener API and data contracts within the accepted product slice.
- Define the workflow state machine, dependency graph representation, roles/capabilities, gates, policy model, approval protocol, recovery semantics, re-plan algorithm, event/audit model, and metrics definitions.
- Define local persistence, restart behavior, evidence bundle schema, scenario runner interface, and reviewer execution path.
- Create architecture views and decision records.
- Produce an implementation decomposition and Phase 03 handoff.

## Required inputs

- `plan/SOT.md`
- `plan/REQUIREMENTS_TRACEABILITY.md`
- `plan/phases/01-acceptance-strategy/ACCEPTANCE_STRATEGY.md`
- `plan/phases/01-acceptance-strategy/SCENARIO_ACCEPTANCE.md`
- `plan/phases/01-acceptance-strategy/RISK_AND_ASSUMPTION_REGISTER.md`
- `plan/phases/01-acceptance-strategy/VERIFICATION.md`

## Design invariants

- Preserve a deterministic, credential-free default evaluator path.
- Use one evolving URL-shortener codebase and one orchestration/evidence model across S-01, S-02, and S-03.
- Treat workflow state, context versions, approvals, decisions, artifacts, and correlated execution events as durable reviewable records.
- Make transition, dependency, freshness, actor, approval, and policy enforcement fail closed and testable.
- Calculate required reliability metrics from execution events rather than static fixtures.
- Support selective descendant invalidation and retained history during re-planning.
- Keep product and infrastructure non-goals out of the critical path.
- Do not claim formal compliance, production availability, or rollback semantics beyond what the design can implement and verify.

## Required work

1. Compare candidate stacks against setup speed, deterministic testing, local durability, graph/state expressiveness, reviewer readability, and delivery risk; record the selection and rejected alternatives.
2. Define architecture views for product components, orchestration/control flow, persistence/evidence, and trust/policy boundaries.
3. Specify the minimal product API/schema and reliability behaviors accepted in Phase 01.
4. Specify workflow/task states and transitions, DAG invariants, gates, fork/join semantics, actor capabilities, and approval behavior.
5. Specify context/artifact versioning, decision lineage, event correlation, metric calculations, retry/fallback/compensation/safe-stop, restart recovery, and re-plan impact rules.
6. Map each S-01/S-02/S-03 behavior and negative path to design elements and future tests.
7. Update requirement traceability from accepted scope to designed/planned components without weakening coverage.
8. Verify feasibility, record design risks and decisions, update `plan/SOT.md`, and write the Phase 03 handoff.

## Completion criteria

- Every accepted Phase 01 component and check maps to a named design element and planned test.
- API, state, event, policy, approval, recovery, metric, and evidence contracts are precise enough for implementation without material invention.
- The design visibly supports graph dependencies, fork/join, entry/exit gates, durable state, lineage, approval, bounded recovery, policy controls, event-derived metrics, and selective governed re-planning.
- S-01, S-02, and S-03 have feasible executable designs and explicit evidence output paths.
- Technology choices are justified against the 2-3 day and credential-free reviewer constraints.
- Implementation tasks have dependencies, acceptance checks, and a credible critical path.

## Prohibited scope

- Do not implement or scaffold application/orchestration code during design.
- Do not expand product scope beyond the Phase 01 minimum winning slice without an explicit requirement/risk rationale and recorded scope decision.
- Do not make optional model, cloud, container, UI, or infrastructure features prerequisites for P0 acceptance.
- Do not silently relax a Phase 01 gate, negative path, evidence contract, or non-goal.
