# Source of Truth

## Current state

- Overall status: Build complete; documentation is the remaining work
- Active phase: Phases 02-06 cancelled as document phases by decision D-013 and
  collapsed into a single build push. Phase 01 remains the implementation spec.
- Assessment PDF reviewed: Yes - all 3 pages extracted and visually inspected
- Technical stack selected: Yes - Python 3.12, FastAPI, SQLite, pytest (D-012)
- Implementation started: Yes - complete and verified

### What is built

- `app/` - URL-shortener service: create, redirect, privacy-conscious analytics,
  health/readiness, SQLite persistence. Verified against a live server including
  restart safety.
- `orchestrator/` - governed execution engine, dependency graph, append-only
  event store, named policies, human approval, bounded recovery, event-derived
  metrics. `orchestrator/contracts.py` is frozen and is the design.
- `scenarios/` - the three replayable runs and their CLI
  (`python -m scenarios.cli run all`).
- `tests/` - 165 tests passing, including an adversarial negative suite written
  by an agent that did not implement the engine.
- `evidence/` - one bundle per scenario, emitted by scenario code only and
  regenerable by re-running.

### Verified scenario results

| Scenario | Terminal state | Key proof |
|---|---|---|
| S-01 greenfield | `succeeded` | fork/join, one bounded retry, one policy denial, human release gate |
| S-02 brownfield | `safe_stopped` | retries exhausted, compensation executed before the safe stop, MTTR computed |
| S-03 ambiguous | `succeeded` | requirement v1 retained beside v2, selective invalidation, controls re-applied |

## Known inputs

- Assessment source document: reviewed during Phase 00 and deliberately not
  copied into this repository. Per decision D-006 the source carries an internal
  classification, so neither the file, a pointer to its location, nor the intake
  notes restating its contents ship with the published submission.
- User goal: Maximize acceptance while making decisions and verification explicit

## Current constraints

- Keep work concise and focused.
- Separate document content from user authorization.
- Verify every executed subtask before advancing.
- Every governance claim in the documentation needs an executable check behind
  it; a claim with no test is deleted or the test is built.

## Open decisions

- None blocking. Remaining work is the documentation set (`README.md`,
  `docs/ARCHITECTURE.md`, `docs/TESTING.md`, `docs/FINAL_SUMMARY.md`).
- Lane E, an optional model-backed executor, was scoped but not built. The
  deterministic credential-free executor is the only one shipped.

## Phase 00 outputs

Phase 00 normalized the assessment source into an intake brief, a clarifications
and risk register, a coverage check, and a handoff. Those notes restate the
source document closely, so per decision D-006 they are retained locally and
excluded from the published repository. `plan/REQUIREMENTS_TRACEABILITY.md` is
the shipped record of that coverage.

- `plan/REQUIREMENTS_TRACEABILITY.md`

## Phase 01 outputs

- `plan/phases/01-acceptance-strategy/ACCEPTANCE_STRATEGY.md`
- `plan/phases/01-acceptance-strategy/SCENARIO_ACCEPTANCE.md`
- `plan/phases/01-acceptance-strategy/RISK_AND_ASSUMPTION_REGISTER.md`
- Updated `plan/REQUIREMENTS_TRACEABILITY.md` with accepted coverage for all 43 baseline items
- `plan/phases/01-acceptance-strategy/VERIFICATION.md`
- `plan/phases/01-acceptance-strategy/PHASE_02_HANDOFF.md`

## Key intake result

- The URL-shortener service is the demonstration domain.
- The critical differentiator is governed, non-linear, stateful workflow orchestration across the full SDLC.
- The solution must visibly prove dependency graphs and gates, fork/join execution, context and decision lineage, human approval, bounded recovery controls, policy guardrails, auditability, reliability metrics, and governed re-planning.
- Sixteen material ambiguities are documented as Phase 01 inputs; none blocks acceptance-strategy work.

## Phases 02-06

Cancelled as document phases by decision D-013. Their planned outputs shipped as
working artifacts instead - see the note at the top of each phase README for
where each one landed.

## Key Phase 01 result

- Minimum product scope: create, redirect, basic privacy-conscious analytics, health/readiness, and bounded reliability behaviors.
- Delivery shape: one evolving codebase and three replayable runs—greenfield baseline, repository-grounded brownfield reliability change, and ambiguous analytics/privacy change with governed re-planning.
- Winning differentiators: policy-enforced orchestration, causal selective re-planning, unified event-derived audit/metrics evidence, and credible before/after codebase evolution.
- Every one of 29 requirements, 6 deliverables, and 8 evaluation signals maps to a planned component, scenario, check, and artifact.
- Every intake ambiguity has a working disposition. Architecture, stack,
  contracts and implementation mechanisms were settled during the build push
  rather than in a Phase 02 document, and are recorded in `plan/DECISIONS.md`
  (D-012 through D-015) and in `orchestrator/contracts.py`.

## Next action

Complete the documentation set, then audit every governance claim in it against
the test suite before packaging the submission.
