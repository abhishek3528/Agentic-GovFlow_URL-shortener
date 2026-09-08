# Phase 01 Risk and Assumption Register

## Resolved working assumptions

These are acceptance-strategy defaults, not architecture or technology choices. Phase 02 may refine implementation details without weakening the stated evidence.

| ID | Phase 01 disposition | Acceptance consequence | Status |
|---|---|---|---|
| A-001 | Core API surface is create, redirect, analytics retrieval, and health/readiness. | All four appear in product smoke and contract evidence; extras are non-goals. | Accepted working assumption |
| A-002 | Analytics is aggregate click count plus at most a small coarse metadata view; raw client identifiers and rich tracking are excluded. | Tests prove counting and agreed privacy/retention behavior. | Accepted working assumption |
| A-003 | Reliability proof is local and behavioral: collision/idempotency correctness, health checks, injected failures, recovery bounds, and event-derived workflow metrics. | No unsupported availability or production SLO claim. | Accepted working assumption |
| A-004 | Agents are explicit capability roles coordinated through inspectable state; default runs are deterministic and model-independent. | Optional model use cannot be required to run or verify scenarios. | Accepted working assumption |
| A-005 | Workflow state and audit events survive process restart in a lightweight local durable store. | Phase 02 must include restart/durability acceptance checks. | Accepted working assumption |
| A-006 | Approval is operable through a documented CLI or API and exposes a paused state; GUI is unnecessary. | Approval and bypass tests must be replayable. | Accepted working assumption |
| A-007 | Breaking API/schema changes, destructive operations, release readiness, and policy exceptions are high impact. | Such transitions fail closed pending attributable human approval. | Accepted working assumption |
| A-008 | Policies cover secret/PII handling, destination/input safety, least-privilege role boundaries, evidence retention, provenance, and high-impact change control. | Allow and deny decisions are stored in the audit history; no formal compliance claim. | Accepted working assumption |
| A-009 | Required rollback proof is workflow compensation and orchestration state restoration. Source-control, database, and deployment rollback are documented boundaries unless directly implemented. | The brownfield failure path must visibly compensate or restore and safe-stop. | Accepted working assumption |
| A-010 | Re-plan proof uses a material approved analytics/privacy clarification that invalidates affected descendants. | Scenario S-03 versions context, preserves history, recomputes impact, and repeats gates. | Accepted working assumption |
| A-011 | All scenarios are replayable runs against one evolving codebase: greenfield baseline, brownfield change, ambiguous re-plan. | Brownfield evidence must reference the established baseline. | Accepted working assumption |
| A-012 | Phase 02 will optimize for a short local command, deterministic tests, readable boundaries, and minimal required dependencies. | No stack choice is made in Phase 01. | Carried to Phase 02 |
| A-013 | Prototype scale is local/single-instance; a credible production evolution is explained but distributed infrastructure is not built. | Performance/reliability assumptions and limitations must be explicit. | Accepted working assumption |
| A-014 | Only security/abuse controls necessary to validate destinations and make scenarios credible are in scope; auth, expiration, aliases, and full abuse prevention are excluded. | Security tests focus on bounded input/URL safety and policy enforcement. | Accepted working assumption |
| A-015 | Reviewer path is local, credential-free by default, with setup, scenario commands, tests, and sample evidence. Container support is optional. | Clean-state quickstart is a release gate. | Accepted working assumption |
| A-016 | The 2-3 day constraint prioritizes full, deep acceptance proof over product and infrastructure breadth. | P0 orchestration controls and replayability displace optional polish. | Accepted working assumption |

## Risk register

| ID | Risk | Likelihood / impact | Prevention or mitigation | Required acceptance evidence | Residual risk |
|---|---|---|---|---|---|
| RSK-001 | Orchestration degrades into a labeled linear script. | High / Critical | Make transitions policy-enforced; require conditional DAG, persisted state, fork/join, recovery, and re-plan behavior. | Graph validation, invalid-transition tests, traces from all three scenarios. | Medium until executable design is verified. |
| RSK-002 | Product or infrastructure breadth consumes the 2-3 day budget. | High / High | Freeze the minimum product slice and non-goals; time-box optional integration. | Trace matrix covers all P0 items; no optional feature is on the critical path. | Medium due to dense orchestration scope. |
| RSK-003 | “Audit-grade,” rollback, or compliance language exceeds proof. | Medium / Critical | Define observable semantics, use bounded claims, and name limitations. | Persisted correlated history; compensation test; generic policy allow/deny tests; final claim audit. | Low if language remains disciplined. |
| RSK-004 | Brownfield scenario appears staged or superficial. | High / High | Establish S-01 first; require repository-grounded impact analysis and red-to-green before/after evidence. | Impact paths, regression fixture, changed artifact lineage, full regression result. | Medium because the repository is intentionally young. |
| RSK-005 | Ambiguity is silently resolved. | Medium / High | Fail the normalization gate on material ambiguity; require explicit assumption approval and later change. | v1/v2 requirement records, approval, invalidation, revised graph, stale-input rejection. | Low. |
| RSK-006 | External model or service prevents reviewer execution. | Medium / High | Deterministic local default; adapter boundary for any optional integration; committed fixtures/evidence examples. | Clean credential-free quickstart and all scenario smoke runs. | Low. |
| RSK-007 | Approval can be bypassed or self-approved by an agent. | Medium / High | Enforce actor capability and state preconditions centrally; fail closed. | Negative bypass tests and attributable human approval event. | Low after policy tests pass. |
| RSK-008 | Parallelism exists only in diagrams. | Medium / High | Emit task start/finish and join evaluation events; hold join until all output contracts pass. | Timing/order trace and incomplete-join negative test. | Low. |
| RSK-009 | Retry hides errors or never terminates. | Medium / High | Per-task budgets, explicit retryability, no silent catch, fallback/compensation, safe-stop. | Transient success and exhausted-budget traces plus metric assertions. | Low. |
| RSK-010 | Reliability metrics are static or misleading. | Medium / Medium | Calculate metrics from the unified event history with definitions and deterministic fixtures. | Calculation unit tests and scenario reconciliation. | Low; prototype sample size remains limited. |
| RSK-011 | Classified source material leaks into deliverables. | Low / High | Keep original PDF outside the workspace/submission; paraphrase requirements; scan deliverables. | Final content/path scan and absence of source PDF. | Low. |
| RSK-012 | Documentation and examples drift from behavior. | Medium / High | Drive evidence from commands; make clean setup and scenario replay release gates. | Clean-state quickstart, docs command tests where feasible, final evaluator audit. | Medium until final packaging. |
| RSK-013 | Stateful persistence is claimed but restart loses workflow or history. | Medium / High | Define durable state contract and recovery semantics in Phase 02; add restart test. | Pause, restart, reload, and resume/inspect test with unchanged event lineage. | Medium pending design. |
| RSK-014 | Re-plan invalidates too much, too little, or permits stale consumption. | Medium / Critical | Use explicit input/output dependencies and context versions; calculate descendant impact; gate on freshness. | Selective invalidation assertions and stale-artifact negative test. | Medium pending graph design. |
| RSK-015 | Unified scenarios/evidence are too complex to explain quickly. | Medium / Medium | Stable scenario names, compact summary views, consistent evidence schema, reviewer quickstart. | Each scenario has one command, concise outcome summary, and artifact index. | Low to Medium. |

## Residual decisions for Phase 02

- Exact architecture, technology stack, repository/module boundaries, and durable store.
- Exact API paths, schemas, status codes, analytics dimensions, and retention interval within the accepted product slice.
- Orchestration state machine, graph representation, role execution mechanism, policy representation, and event schema.
- Concrete local commands, packaging method, and whether optional container support fits without jeopardizing P0 evidence.
- Precise prototype targets and metric definitions, while retaining the required named metrics and candid scale boundary.
