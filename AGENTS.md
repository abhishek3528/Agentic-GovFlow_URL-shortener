# Agent Brief

Read this before touching any file. It is the operating contract for every agent
working in this repository.

## What this project is

A governed agentic SDLC orchestration engine, demonstrated by building and then
evolving a URL-shortener service. The orchestration layer is the primary
deliverable; the URL shortener is the realistic work it governs.

The graded claim is that this is **non-linear, stateful, governed execution** -
an explicit dependency graph with entry/exit gates, parallel paths that
synchronize, preserved decision lineage, human approval on high-impact actions,
bounded retry/fallback/rollback/safe-stop, policy guardrails, audit-grade event
history, reliability metrics, and governed re-planning when upstream
requirements change.

## Hard rules

1. **`orchestrator/contracts.py` is FROZEN.** Do not modify it. Do not add
   fields. Every component is built against it in parallel, so a change there
   breaks work you cannot see. If you are convinced it must change, **stop and
   report** rather than editing.

2. **Interpreter is the venv, always:**
   ```
   D:\URL-Project\.venv\Scripts\python.exe
   ```
   Never bare `python` or `python3` - two interpreters exist on this machine and
   the PATH default is not the one with dependencies installed.

3. **Time and identity are injected.** Use `orchestrator.clock`. Never call
   `datetime.now()`, `time.time()`, `uuid4()`, or `random` without a seed.
   Scenario runs must be byte-stable in structure across repeated runs.

4. **Never hand-author anything under `evidence/`.** It is emitted by scenario
   code only. A bundle that no code path produces reads as fabricated evidence
   and discredits the whole submission. `evidence/` is gitignored precisely
   because it must always be regenerable by re-running.

5. **Executors do work; the engine decides governance.** A `TaskExecutor`
   implementation must not mutate engine state, write events, assign artifact
   ids or versions, or decide gate/policy outcomes.

6. **Gates fail closed.** Missing evidence, a missing branch output, an
   unapproved high-impact task, or an unevaluated policy is a denial, not a
   pass.

7. **Do not build the non-goals.** See
   `plan/phases/01-acceptance-strategy/ACCEPTANCE_STRATEGY.md` - "Explicit
   non-goals". No UI, no Docker, no rate limiting, no custom aliases, no QR
   codes, no auth platform. Scope discipline is itself graded.

8. **Run the tests before reporting done:**
   ```
   D:\URL-Project\.venv\Scripts\python.exe -m pytest tests/ -q
   ```

## The spec

| Document | Use it for |
|---|---|
| `plan/phases/01-acceptance-strategy/ACCEPTANCE_STRATEGY.md` | Scope, non-goals, acceptance principles |
| `plan/phases/01-acceptance-strategy/SCENARIO_ACCEPTANCE.md` | What each of the three scenarios must prove |
| `orchestrator/contracts.py` | The design. Types, state machines, invariants |
| `plan/DECISIONS.md` | Decisions already made - do not relitigate |

Phases 02-06 in `plan/` are cancelled (decision D-013). Do not write design
documents. `orchestrator/contracts.py` is the design.

## Lanes

Lanes A-C are independent and may run in parallel. They all build against the
frozen contracts.

### Lane A - orchestrator core
`orchestrator/engine.py`, `orchestrator/graph.py`, `orchestrator/events.py`

- DAG construction, cycle detection, topological readiness
- Task and run state machines, enforced through `is_legal_task_transition` /
  `is_legal_run_transition` - never move state without asking
- Append-only event store with monotonic per-run `seq`, persisted as JSONL
- Entry/exit gate evaluation
- Fork/join: a task with multiple `depends_on` cannot start until every declared
  `consumes` artifact exists, is fresh (`stale is False`), and its producer
  passed its exit gates
- Selective invalidation: given a changed `ContextVersion`, walk the lineage
  graph, mark only affected descendants `STALE`, retain everything else, emit a
  new `Plan` revision that `supersedes` the prior one

**Done when:** a multi-task DAG with a fork and a join executes end to end,
emits a correct event stream, and refuses an illegal transition.

### Lane B - URL shortener service
`app/`

- `POST /links` create (validated destination, deterministic collision handling,
  idempotent creation), `GET /{code}` redirect, `GET /links/{code}/stats`
  analytics, `GET /health` + `GET /ready`
- SQLite persistence, restart-safe
- OpenAPI contract emitted from FastAPI
- Unit + integration tests: valid, invalid, collision, idempotency, not-found,
  health

**Done when:** the service starts, the full create -> redirect -> stats path
works against a live server, and tests cover the negative cases above.

### Lane C - controls
`orchestrator/policy.py`, `orchestrator/approval.py`, `orchestrator/recovery.py`

- Named policies producing `PolicyDecision` for both allow and deny: URL safety
  (scheme allowlist, no `javascript:`/`data:`), privacy (no raw client
  identifiers retained), change control (breaking change classification),
  evidence retention
- Approval flow: high-impact tasks pause in `AWAITING_APPROVAL`, emit
  `APPROVAL_REQUESTED`, and resume only on a human-actor `Approval`
- Recovery: bounded retry honouring `Task.retry_budget`, non-transient failures
  skipping retries, named compensating action on terminal failure, and
  `SAFE_STOPPED` when recovery is exhausted - never a success claim
- `RunMetrics` computed **from the event stream only**

**Done when:** each control has a code path that returns a denial, and metrics
are derived from events rather than recorded alongside them.

### Lane D - adversarial negative tests

**Must be written by an agent that did NOT implement lanes A or C.** Tests
written by the implementer tend to assert the implementation back to itself.

Each test must be shown to FAIL when its control is disabled:

- Illegal state transition rejected
- Approval bypass rejected (agent actor cannot grant approval)
- Join advancing with a missing branch output rejected
- Stale artifact consumption rejected
- Retry budget exhaustion reaching `SAFE_STOPPED`, not `SUCCEEDED`
- Unsafe URL denied by policy while permitted work continues
- Re-plan preserving v1 history rather than overwriting it

### Lane E - optional model-backed executor (after A-C land)
`orchestrator/executors/llm.py`

One optional `TaskExecutor` behind an env var, **off by default**, proving the
control plane is model-agnostic. The credential-free deterministic path must
remain the default reviewer path.

## Scenario chain - strictly sequential

S-01 -> S-02 -> S-03 cannot be parallelized. S-02 operates on the codebase S-01
produced; S-03 evolves what S-02 produced. That evolution is the point.

- **S-01 greenfield:** build the baseline. Fork into two implementation paths,
  join before integrated validation. One transient failure recovered inside its
  retry budget. One unsafe-URL policy denial. Human-owned release gate.
- **S-02 brownfield:** repository-grounded impact analysis *before* planning, a
  red test reproducing a collision/idempotency defect, then a gated fix with
  green before/after proof. One persistent failure exhausting retries, invoking
  compensation, ending safe-stopped.
- **S-03 ambiguous:** "improve link analytics" - surface ambiguities with
  proposed assumptions, pause for human clarification, then a v2 requirement
  disallowing raw client identifiers. Invalidate only affected descendants,
  retain the rest, re-apply gates, keep v1 history intact.

Each emits an evidence bundle under `evidence/<run_id>/`.

## Documentation - last, one pass, from finished code

`README.md` (one obvious command path), `docs/ARCHITECTURE.md`,
`docs/TESTING.md`, `docs/FINAL_SUMMARY.md` (plan and rationale, artifacts,
risks/trade-offs/validation, assumptions, limitations).

**Every governance claim in the docs needs an executable check behind it.** If a
claim has no test, delete the claim or build the test. An unbacked claim a
reviewer probes and finds hollow costs more than the claim was worth.
