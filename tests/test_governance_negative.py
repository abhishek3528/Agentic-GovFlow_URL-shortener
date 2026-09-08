"""Adversarial negative tests for the governance controls (lane D).

These tests are written by an agent that did not implement lanes A-C. Every
assertion here is derived from the specification -- ``AGENTS.md``, the frozen
``orchestrator/contracts.py``, ``ACCEPTANCE_STRATEGY.md`` and
``SCENARIO_ACCEPTANCE.md`` -- and never from reading the engine, policy,
approval or recovery implementations. A test that mirrors the implementation
asserts the implementation back to itself and proves nothing, so the discipline
matters more than the coverage number.

**The paired-test convention.** AGENTS.md requires that each negative test "be
shown to FAIL when its control is disabled". A negative test that passes because
the situation never arose is indistinguishable from one that passes because the
control worked. So every control here has two tests:

``test_<invariant>``
    The illegal thing is attempted and must be refused.

``test_<invariant>_control_is_load_bearing``
    The same code path is exercised with the control's input flipped -- a
    permitted URL instead of a prohibited one, a failure count inside the retry
    budget instead of beyond it, a human approver instead of an agent -- or with
    the documented contract predicate stubbed permissive. The outcome must
    flip. If it does not, the paired negative test was passing for an incidental
    reason and its result is worthless.

Flipping the control's *input* is preferred over patching internals: it needs no
knowledge of how the control is implemented, so these tests keep their meaning
across refactors of lanes A-C.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator import engine as engine_module
from orchestrator.clock import FixedClock, SeededIdGen
from orchestrator.contracts import (
    LEGAL_RUN_TRANSITIONS,
    LEGAL_TASK_TRANSITIONS,
    TERMINAL_RUN_STATES,
    Actor,
    ActorKind,
    Approval,
    ContextVersion,
    Event,
    EventType,
    Gate,
    GateKind,
    ImpactClass,
    Plan,
    RunState,
    Stage,
    Task,
    TaskState,
    is_legal_run_transition,
    is_legal_task_transition,
)
from orchestrator.engine import Engine, EngineError, IllegalTransitionError
from orchestrator.events import EventStore, EventStoreError
from orchestrator.executor import DeterministicExecutor, ScriptedFailureExecutor, TaskOutput
from orchestrator.graph import DependencyGraph, GraphValidationError
from orchestrator.policy import PolicyEngine
from orchestrator.recovery import RecoveryController, compute_run_metrics

T0 = "2026-01-01T00:00:00+00:00"
T1 = "2026-01-02T00:00:00+00:00"

HUMAN = Actor(kind=ActorKind.HUMAN, id="reviewer:alex")
AGENT = Actor(kind=ActorKind.AGENT, id="agent:planner")


# --------------------------------------------------------------------------
# Fixtures and builders
# --------------------------------------------------------------------------


def task(task_id: str, **overrides) -> Task:
    """A minimal valid task; overrides carry whatever the test is probing."""
    overrides.setdefault("name", task_id)
    overrides.setdefault("stage", Stage.IMPLEMENTATION)
    overrides.setdefault("capability", "engineer")
    return Task(id=task_id, **overrides)


def plan(*tasks: Task, revision: int = 1, context_version: int = 1) -> Plan:
    return Plan(
        revision=revision,
        context_version=context_version,
        tasks=tuple(tasks),
        created_at=T0,
    )


def engine(*tasks: Task, **overrides) -> Engine:
    """An engine on a deterministic clock, so failures are reproducible."""
    overrides.setdefault("executor", DeterministicExecutor())
    overrides.setdefault("clock", FixedClock())
    overrides.setdefault("id_gen", SeededIdGen())
    overrides.setdefault("run_id", "run-negative")
    plan_revision = overrides.pop("plan", None) or plan(*tasks)
    return Engine(plan_revision, **overrides)


def context(version: int, **overrides) -> ContextVersion:
    overrides.setdefault("raw_requirement", f"requirement v{version}")
    overrides.setdefault("normalized_problem", f"normalized v{version}")
    overrides.setdefault("created_at", T1)
    return ContextVersion(version=version, **overrides)


def event_snapshot(source: Engine) -> list[dict]:
    """A comparable, order-preserving copy of the whole event stream."""
    return [record.model_dump(mode="json") for record in source.events]


def states(source: Engine) -> dict[str, TaskState]:
    return {t.id: t.state for t in source.current_plan.tasks}


def fork_join_tasks() -> tuple[Task, Task, Task, Task]:
    """seed -> (branch_b | branch_c) -> join.

    The join declares both branch outputs in ``consumes``, which per the
    ``Task`` contract is what makes it a real synchronisation point rather than
    a task that merely happens to be ordered last.
    """
    seed = task("seed", produces=("seed_out",), stage=Stage.DESIGN)
    branch_b = task("branch_b", depends_on=("seed",), consumes=("seed_out",), produces=("b_out",))
    branch_c = task("branch_c", depends_on=("seed",), consumes=("seed_out",), produces=("c_out",))
    join = task(
        "join",
        depends_on=("branch_b", "branch_c"),
        consumes=("b_out", "c_out"),
        produces=("integrated",),
        stage=Stage.TESTING,
    )
    return seed, branch_b, branch_c, join


# ==========================================================================
# 1. Illegal state transitions are rejected
#
# contracts.py: "Transitions are explicit. LEGAL_TASK_TRANSITIONS and
# LEGAL_RUN_TRANSITIONS are the whole state machine... Illegal moves fail
# closed."
# ==========================================================================


ILLEGAL_TASK_MOVES = [
    # Work may not begin without passing through readiness.
    (TaskState.PENDING, TaskState.RUNNING),
    # Success may never be claimed without execution.
    (TaskState.PENDING, TaskState.SUCCEEDED),
    (TaskState.READY, TaskState.SUCCEEDED),
    (TaskState.AWAITING_APPROVAL, TaskState.SUCCEEDED),
    (TaskState.RETRYING, TaskState.SUCCEEDED),
    # A task that exhausted its budget cannot be talked back into success.
    (TaskState.FAILED, TaskState.SUCCEEDED),
    (TaskState.FAILED, TaskState.RUNNING),
    (TaskState.FAILED, TaskState.RETRYING),
    # A gate denial is terminal until a re-plan supersedes it.
    (TaskState.BLOCKED, TaskState.RUNNING),
    (TaskState.BLOCKED, TaskState.SUCCEEDED),
    # Completed work cannot silently restart.
    (TaskState.SUCCEEDED, TaskState.RUNNING),
    (TaskState.SUCCEEDED, TaskState.FAILED),
    (TaskState.COMPENSATED, TaskState.SUCCEEDED),
    # Invalidated work rejoins only as PENDING in a new plan revision.
    (TaskState.STALE, TaskState.RUNNING),
    (TaskState.STALE, TaskState.SUCCEEDED),
    (TaskState.SKIPPED, TaskState.RUNNING),
    # Approval cannot be re-entered to launder a decision.
    (TaskState.RUNNING, TaskState.AWAITING_APPROVAL),
    (TaskState.SUCCEEDED, TaskState.AWAITING_APPROVAL),
]


@pytest.mark.parametrize(("src", "dst"), ILLEGAL_TASK_MOVES)
def test_illegal_task_transition_rejected_by_contract(src: TaskState, dst: TaskState) -> None:
    assert not is_legal_task_transition(src, dst), (
        f"{src.value} -> {dst.value} must be illegal: it would let a task reach a state "
        "without the governance the intermediate states exist to impose"
    )


def test_illegal_task_transition_control_is_load_bearing() -> None:
    """The predicate must not be a rubber stamp that returns False for everything.

    Without this, the parametrised test above would pass against a
    ``lambda src, dst: False`` implementation while blocking every legal move.
    """
    legal_moves = [
        (TaskState.PENDING, TaskState.READY),
        (TaskState.READY, TaskState.RUNNING),
        (TaskState.READY, TaskState.AWAITING_APPROVAL),
        (TaskState.RUNNING, TaskState.SUCCEEDED),
        (TaskState.RUNNING, TaskState.RETRYING),
        (TaskState.RETRYING, TaskState.RUNNING),
        (TaskState.FAILED, TaskState.COMPENSATED),
        (TaskState.STALE, TaskState.PENDING),
    ]
    for src, dst in legal_moves:
        assert is_legal_task_transition(src, dst), f"{src.value} -> {dst.value} must be legal"


def test_task_state_machine_is_total_and_fails_closed() -> None:
    """Every state needs an explicit row; a missing row is an unreviewed hole."""
    for state in TaskState:
        assert state in LEGAL_TASK_TRANSITIONS, f"{state.value} has no declared transitions"
    # An unknown source must deny rather than default-allow.
    assert not is_legal_task_transition("not-a-state", TaskState.SUCCEEDED)  # type: ignore[arg-type]


def test_terminal_run_states_have_no_exit() -> None:
    """SAFE_STOPPED must never be relabelled as success after the fact."""
    for state in TERMINAL_RUN_STATES:
        assert LEGAL_RUN_TRANSITIONS[state] == frozenset(), f"{state.value} must be terminal"
    assert not is_legal_run_transition(RunState.SAFE_STOPPED, RunState.SUCCEEDED)
    assert not is_legal_run_transition(RunState.FAILED, RunState.SUCCEEDED)
    assert not is_legal_run_transition(RunState.SUCCEEDED, RunState.RUNNING)
    assert not is_legal_run_transition(RunState.PENDING, RunState.SUCCEEDED)


def test_engine_refuses_an_illegal_task_transition_without_touching_state() -> None:
    """A refused transition must leave no trace: no state change, no event."""
    governed = engine(task("solo", produces=("out",)))
    governed.start()
    before_state = governed.task("solo").state
    before_events = event_snapshot(governed)

    with pytest.raises(IllegalTransitionError):
        governed.transition_task("solo", TaskState.SUCCEEDED, actor=AGENT)

    assert governed.task("solo").state is before_state
    assert event_snapshot(governed) == before_events, (
        "a rejected transition must not append to the audit history"
    )


def test_engine_refuses_a_direct_run_success_claim() -> None:
    """An agent must not be able to declare the run successful out of band."""
    governed = engine(task("solo", produces=("out",)))
    with pytest.raises(IllegalTransitionError):
        governed.transition_run(RunState.SUCCEEDED, actor=AGENT, reason="looks fine to me")
    assert governed.run_state is not RunState.SUCCEEDED


def test_engine_illegal_transition_control_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the legality table and the specific refusal must disappear.

    contracts.py states the engine "asks this module whether a move is legal; it
    does not decide for itself". Stubbing that predicate permissive is therefore
    the documented way to disable this control. A second, independent refusal
    (direct transitions are not part of the public workflow) may still fire --
    that is defence in depth, not this control -- so only the
    ``IllegalTransitionError`` is required to vanish.
    """
    governed = engine(task("solo", produces=("out",)))
    governed.start()
    monkeypatch.setattr(engine_module, "is_legal_task_transition", lambda src, dst: True)

    illegal_error: IllegalTransitionError | None = None
    try:
        governed.transition_task("solo", TaskState.SUCCEEDED, actor=AGENT)
    except IllegalTransitionError as exc:  # pragma: no cover - the failure we assert against
        illegal_error = exc
    except EngineError:
        pass  # an independent guard on direct mutation; not the control under test

    assert illegal_error is None, (
        "with the legality table disabled the transition was still rejected as illegal, so "
        "test_engine_refuses_an_illegal_task_transition_without_touching_state does not "
        "actually prove the frozen state machine is enforced"
    )


# ==========================================================================
# 2. Approval bypass is rejected
#
# contracts.py: "Approval is attributable to a human. Approval rejects a
# non-human actor at construction time, so an agent cannot approve its own
# high-impact work even if the engine has a bug."
# SCENARIO_ACCEPTANCE S-01: "Final release readiness ... cannot be reached by an
# agent-only transition."
# ==========================================================================


@pytest.mark.parametrize("actor", [AGENT, Actor(kind=ActorKind.SYSTEM, id="system")])
def test_approval_construction_rejects_a_non_human_actor(actor: Actor) -> None:
    with pytest.raises(ValidationError):
        Approval(
            id="approval-1",
            task_id="release",
            granted=True,
            actor=actor,
            rationale="approving my own work",
            decided_at=T0,
        )


def test_approval_construction_control_is_load_bearing() -> None:
    """The same record with a human actor must construct.

    Otherwise the rejection above could be caused by any unrelated field and
    would prove nothing about actor attribution.
    """
    approval = Approval(
        id="approval-1",
        task_id="release",
        granted=True,
        actor=HUMAN,
        rationale="reviewed the release checklist",
        decided_at=T0,
    )
    assert approval.actor.kind is ActorKind.HUMAN


def test_approval_record_is_immutable_after_the_fact() -> None:
    """A granted approval cannot be re-pointed at a different task or actor."""
    approval = Approval(
        id="approval-1", task_id="release", granted=True, actor=HUMAN,
        rationale="ok", decided_at=T0,
    )
    with pytest.raises(ValidationError):
        approval.task_id = "some-other-task"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        approval.actor = AGENT  # type: ignore[misc]


def test_high_impact_work_requires_approval_by_classification() -> None:
    assert ImpactClass.HIGH.requires_approval
    assert ImpactClass.BREAKING.requires_approval
    assert task("t", impact=ImpactClass.BREAKING).requires_approval
    # Control: low-risk work must not be gated, or the gate means nothing.
    assert not ImpactClass.LOW.requires_approval
    assert not ImpactClass.MEDIUM.requires_approval
    assert not task("t", impact=ImpactClass.LOW).requires_approval


def test_high_impact_task_pauses_and_the_run_cannot_succeed_unapproved() -> None:
    governed = engine(task("release", impact=ImpactClass.HIGH, produces=("release_note",),
                           stage=Stage.RELEASE_READINESS))
    result = governed.run(actor=AGENT)

    assert result is not RunState.SUCCEEDED
    assert governed.run_state is RunState.AWAITING_APPROVAL
    assert governed.task("release").state is TaskState.AWAITING_APPROVAL
    assert any(e.type is EventType.APPROVAL_REQUESTED for e in governed.events)
    assert not governed.artifacts, "a paused task must not have produced its output"


def test_engine_refuses_an_agent_granted_approval() -> None:
    """An approval whose actor is an agent must never release a paused task.

    The pydantic validator on ``Approval`` is the first line of defence, but it
    is not the only way an ``Approval`` reaches the engine: ``model_construct``,
    ``model_copy(update=...)`` and deserialisation of a persisted record all skip
    field validators. The engine is the component that grants execution rights,
    so it must re-check attribution rather than trust that whoever built the
    record went through the validating constructor.

    DO NOT WEAKEN THIS TEST. If it fails, the fix belongs in the engine's
    approval handling -- a check that ``approval.actor.kind is ActorKind.HUMAN``
    before resuming -- not here. AGENTS.md lane D names "approval bypass
    rejected (agent actor cannot grant approval)" as a required negative test,
    and ACCEPTANCE_STRATEGY.md rank 2 lists "cosmetic approval" as a failure
    signal.
    """
    governed = engine(task("release", impact=ImpactClass.HIGH, produces=("release_note",),
                           stage=Stage.RELEASE_READINESS))
    governed.run(actor=AGENT)
    assert governed.task("release").state is TaskState.AWAITING_APPROVAL

    self_approval = Approval.model_construct(
        id="approval-forged",
        task_id="release",
        granted=True,
        actor=AGENT,
        rationale="the agent approving its own high-impact work",
        decided_at=T0,
    )
    assert self_approval.actor.kind is ActorKind.AGENT, "the test fixture must be a bypass attempt"

    try:
        governed.decide_approval(self_approval)
    except (EngineError, ValidationError, ValueError):
        pass  # refusing loudly is the correct behaviour

    assert governed.task("release").state is not TaskState.SUCCEEDED
    assert governed.run_state is not RunState.SUCCEEDED
    assert not any(a.actor.kind is not ActorKind.HUMAN for a in governed.approvals), (
        "a non-human approval was recorded as if it were a governance decision"
    )


def test_approval_from_a_human_control_is_load_bearing() -> None:
    """A genuine human approval must resume the run.

    This is what makes the bypass test meaningful: the pause is released by
    *who* approved, not by some unrelated precondition.
    """
    governed = engine(task("release", impact=ImpactClass.HIGH, produces=("release_note",),
                           stage=Stage.RELEASE_READINESS))
    governed.run(actor=AGENT)

    result = governed.decide_approval(
        Approval(id="approval-1", task_id="release", granted=True, actor=HUMAN,
                 rationale="release checklist reviewed", decided_at=T0)
    )

    assert result is RunState.SUCCEEDED
    assert governed.task("release").state is TaskState.SUCCEEDED
    assert governed.metrics.approvals_requested == 1


def test_a_withheld_approval_blocks_rather_than_proceeds() -> None:
    """Declining is a denial, not a no-op that lets the work continue anyway."""
    governed = engine(task("release", impact=ImpactClass.HIGH, produces=("release_note",),
                           stage=Stage.RELEASE_READINESS))
    governed.run(actor=AGENT)
    governed.decide_approval(
        Approval(id="approval-1", task_id="release", granted=False, actor=HUMAN,
                 rationale="release checklist incomplete", decided_at=T0)
    )
    assert governed.task("release").state is TaskState.BLOCKED
    assert governed.run_state is not RunState.SUCCEEDED
    assert not governed.artifacts


def test_an_approval_for_another_task_does_not_release_this_one() -> None:
    """Approval is correlated. A signature on a different task is not consent."""
    governed = engine(
        task("release", impact=ImpactClass.HIGH, produces=("release_note",),
             stage=Stage.RELEASE_READINESS),
    )
    governed.run(actor=AGENT)

    try:
        governed.decide_approval(
            Approval(id="approval-1", task_id="a-different-task", granted=True, actor=HUMAN,
                     rationale="approving something else entirely", decided_at=T0)
        )
    except (EngineError, KeyError):
        pass  # refusing a mis-correlated approval is correct

    assert governed.task("release").state is TaskState.AWAITING_APPROVAL
    assert governed.run_state is not RunState.SUCCEEDED


# ==========================================================================
# 3. A join cannot advance with a missing branch output
#
# AGENTS.md lane A: "a task with multiple depends_on cannot start until every
# declared consumes artifact exists, is fresh (stale is False), and its producer
# passed its exit gates."
# AGENTS.md rule 6: "Gates fail closed. Missing evidence, a missing branch
# output ... is a denial, not a pass."
# ==========================================================================


def test_join_does_not_advance_when_a_branch_produces_no_output() -> None:
    seed, branch_b, branch_c, join = fork_join_tasks()
    silent_branch = DeterministicExecutor(
        handlers={"branch_c": lambda t, inputs: TaskOutput(succeeded=True, summary="no output", artifacts={})}
    )
    governed = engine(seed, branch_b, branch_c, join, executor=silent_branch)

    result = governed.run()

    assert result is not RunState.SUCCEEDED
    assert governed.task("join").state is not TaskState.SUCCEEDED
    assert governed.task("join").state is not TaskState.RUNNING
    assert governed.task("join").attempts == 0, "the join must never have been executed"
    produced = {a.name for a in governed.artifacts}
    assert "integrated" not in produced, "the join emitted its output without its inputs"
    assert "c_out" not in produced


def test_join_missing_branch_output_control_is_load_bearing() -> None:
    """With both branch outputs present, the same join must complete.

    This proves the refusal above was caused by the missing artifact and not by
    the graph shape, the stage assignment, or an unrelated gate.
    """
    governed = engine(*fork_join_tasks())

    assert governed.run() is RunState.SUCCEEDED
    assert governed.task("join").state is TaskState.SUCCEEDED
    assert "integrated" in {a.name for a in governed.artifacts}


def test_join_cannot_be_executed_while_a_branch_is_still_incomplete() -> None:
    """Direct execution must not be a way around the synchronisation point."""
    seed, branch_b, branch_c, join = fork_join_tasks()
    governed = engine(seed, branch_b, branch_c, join)
    governed.start()
    governed.promote_ready_tasks()
    governed.execute_task("seed")
    governed.promote_ready_tasks()
    governed.execute_task("branch_b")  # branch_c deliberately left unrun

    with pytest.raises(EngineError):
        governed.execute_task("join")

    assert governed.task("join").state is not TaskState.SUCCEEDED
    assert governed.task("branch_c").state is not TaskState.SUCCEEDED


def test_join_does_not_advance_when_a_branch_fails_its_exit_gate() -> None:
    """A branch whose producer was denied at its exit gate is not a satisfied input."""
    seed, branch_b, branch_c, join = fork_join_tasks()
    branch_c = branch_c.model_copy(
        update={"exit_gates": (Gate(id="c-review", kind=GateKind.EXIT,
                                    description="branch c output reviewed"),)}
    )

    def deny_branch_c(gate: Gate, t: Task, artifacts) -> tuple[bool, str]:
        return (False, "branch c output was not reviewed")

    governed = engine(seed, branch_b, branch_c, join, gate_evaluator=deny_branch_c)
    result = governed.run()

    assert result is not RunState.SUCCEEDED
    assert governed.task("branch_c").state is TaskState.BLOCKED
    assert governed.task("join").state is not TaskState.SUCCEEDED
    assert governed.metrics.gate_denials >= 1


def test_graph_rejects_a_join_whose_input_has_no_producer() -> None:
    """A join declaring an input nothing produces is a plan bug, caught up front."""
    with pytest.raises(GraphValidationError):
        DependencyGraph(plan(
            task("a", produces=("a_out",)),
            task("join", depends_on=("a",), consumes=("a_out", "never_produced")),
        ))


def test_graph_rejects_a_consumed_artifact_produced_outside_the_dependency_chain() -> None:
    """Consuming an artifact whose producer is not an ancestor is an unordered read."""
    with pytest.raises(GraphValidationError):
        DependencyGraph(plan(
            task("producer", produces=("spec",)),
            task("trigger", produces=("trigger_out",)),
            task("consumer", depends_on=("trigger",), consumes=("spec", "trigger_out")),
        ))


# ==========================================================================
# 4. Stale artifacts cannot be consumed
#
# contracts.py Artifact: "``stale`` is the only mutable field: a re-plan flips
# it, and the engine refuses to let a downstream task consume a stale input."
# SCENARIO_ACCEPTANCE S-03: "stale artifacts cannot be consumed."
# ==========================================================================


def pipeline_tasks() -> tuple[Task, Task, Task]:
    """producer -> consumer, plus an independent ``retained`` branch.

    ``retained`` sits outside the producer's lineage, so a re-plan seeded on
    either side has a visibly different blast radius.
    """
    return (
        task("producer", produces=("spec",), stage=Stage.DESIGN),
        task("consumer", depends_on=("producer",), consumes=("spec",), produces=("code",)),
        task("retained", produces=("docs",), stage=Stage.DOCUMENTATION),
    )


def executed_pipeline(*, run_consumer: bool) -> Engine:
    """Drive the pipeline task by task, leaving the run open for a re-plan."""
    governed = engine(*pipeline_tasks())
    governed.start()
    governed.promote_ready_tasks()
    governed.execute_task("producer")
    governed.execute_task("retained")
    if run_consumer:
        governed.promote_ready_tasks()
        governed.execute_task("consumer")
    return governed


def replanned_pipeline() -> tuple[Engine, ContextVersion]:
    """Run producer + retained, then invalidate the producer's output.

    The consumer is deliberately left unrun so the stale-input tests can attack
    its first execution.
    """
    governed = executed_pipeline(run_consumer=False)
    revised = context(2, supersedes=1, change_reason="upstream requirement changed")
    governed.replan(revised, changed_task_ids=["producer"])
    return governed, revised


def test_replan_marks_the_superseded_artifact_stale_and_keeps_it() -> None:
    governed, _ = replanned_pipeline()
    spec_versions = [a for a in governed.artifacts if a.name == "spec"]

    assert len(spec_versions) == 1
    assert spec_versions[0].stale is True, "the superseded input was not marked stale"
    assert any(e.type is EventType.ARTIFACT_INVALIDATED for e in governed.events), (
        "the flip must be accompanied by an event so the prior state stays reconstructible"
    )


def test_consumer_cannot_execute_while_its_input_is_invalidated() -> None:
    governed, _ = replanned_pipeline()
    governed.promote_ready_tasks()

    with pytest.raises(EngineError):
        governed.execute_task("consumer")

    assert governed.task("consumer").state is not TaskState.SUCCEEDED
    assert governed.task("consumer").attempts == 0
    assert "code" not in {a.name for a in governed.artifacts}


def test_stale_input_control_is_load_bearing() -> None:
    """Without the invalidation, the identical call must succeed.

    Same plan, same executor, same call sequence -- the only difference is that
    no re-plan invalidated the input. If the consumer fails here too, the test
    above was passing for an unrelated reason.
    """
    governed = executed_pipeline(run_consumer=False)
    governed.promote_ready_tasks()

    assert governed.execute_task("consumer") is TaskState.SUCCEEDED
    assert "code" in {a.name for a in governed.artifacts}


def test_no_task_is_ever_offered_a_stale_artifact_as_an_input() -> None:
    """Watch every artifact the engine hands a task across a run with a re-plan.

    The gate evaluator receives the exact ``Mapping[str, Artifact]`` the engine
    considers that task's governed inputs, which makes it the honest place to
    assert the freshness rule from the outside.
    """
    offered: list[tuple[str, str, bool]] = []

    def record(gate: Gate, t: Task, artifacts) -> bool:
        offered.extend((t.id, name, art.stale) for name, art in artifacts.items())
        return True

    entry = Gate(id="inputs-fresh", kind=GateKind.ENTRY, description="declared inputs are fresh")
    exit_ = Gate(id="outputs-present", kind=GateKind.EXIT, description="declared outputs exist")
    producer = task("producer", produces=("spec",), stage=Stage.DESIGN,
                    entry_gates=(entry,), exit_gates=(exit_,))
    consumer = task("consumer", depends_on=("producer",), consumes=("spec",), produces=("code",),
                    entry_gates=(entry,), exit_gates=(exit_,))
    governed = engine(producer, consumer, gate_evaluator=record)
    governed.start()
    governed.promote_ready_tasks()
    governed.execute_task("producer")
    governed.replan(context(2, supersedes=1, change_reason="upstream requirement changed"),
                    changed_task_ids=["producer"])
    governed.run()

    assert offered, "vacuous: the gate evaluator was never handed any artifact to inspect"
    stale_offers = [entry_ for entry_ in offered if entry_[2]]
    assert not stale_offers, f"a stale artifact was offered as a task input: {stale_offers}"


def test_reexecution_after_invalidation_produces_a_fresh_version() -> None:
    """The consumer must end up reading a re-produced input, not the stale one."""
    governed, _ = replanned_pipeline()
    governed.run()

    spec_versions = sorted((a.version, a.stale) for a in governed.artifacts if a.name == "spec")
    assert spec_versions == [(1, True), (2, False)], (
        "expected the stale v1 to be retained alongside a fresh v2"
    )
    assert governed.task("consumer").state is TaskState.SUCCEEDED

    invalidated = next(i for i, e in enumerate(governed.events)
                       if e.type is EventType.ARTIFACT_INVALIDATED)
    refreshed = next(i for i, e in enumerate(governed.events)
                     if e.type is EventType.ARTIFACT_PRODUCED and i > invalidated)
    consumed = next(i for i, e in enumerate(governed.events)
                    if e.type is EventType.TASK_STATE_CHANGED
                    and e.task_id == "consumer"
                    and e.payload.get("to") == TaskState.RUNNING.value
                    and i > invalidated)
    assert refreshed < consumed, "the consumer ran before its input was re-produced"


# ==========================================================================
# 5. Retry exhaustion reaches SAFE_STOPPED, never SUCCEEDED
#
# AGENTS.md lane C: "bounded retry honouring Task.retry_budget, non-transient
# failures skipping retries, named compensating action on terminal failure, and
# SAFE_STOPPED when recovery is exhausted - never a success claim."
# ==========================================================================


def always_failing(task_id: str = "flaky", budget: int = 2, **overrides) -> Engine:
    """A task whose every attempt fails transiently, so the budget is what stops it."""
    overrides.setdefault("produces", ("out",))
    overrides.setdefault("compensation", "restore_snapshot")
    failing = task(task_id, retry_budget=budget, **overrides)
    return engine(
        failing,
        executor=ScriptedFailureExecutor(DeterministicExecutor(), failures={task_id: 99}),
        recovery_controller=RecoveryController(compensations={"restore_snapshot": lambda t, r: True}),
    )


def test_retry_exhaustion_safe_stops_and_never_claims_success() -> None:
    governed = always_failing(budget=2)

    result = governed.run()

    assert result is RunState.SAFE_STOPPED
    assert result is not RunState.SUCCEEDED
    assert governed.task("flaky").state is not TaskState.SUCCEEDED
    assert governed.task("flaky").state in (TaskState.FAILED, TaskState.COMPENSATED)
    assert any(e.type is EventType.SAFE_STOP_TRIGGERED for e in governed.events)
    assert governed.metrics.success_rate == 0.0
    assert governed.metrics.tasks_succeeded == 0


def test_retry_attempts_are_bounded_by_the_declared_budget() -> None:
    """An unbounded retry loop is called out as a failure signal in the strategy."""
    governed = always_failing(budget=2)
    governed.run()

    assert governed.task("flaky").attempts == 3, "expected the first attempt plus 2 retries"
    assert governed.task("flaky").retries_remaining == 0
    assert governed.metrics.retry_count == 2
    assert sum(1 for e in governed.events if e.type is EventType.RETRY_ATTEMPTED) == 2


def test_a_zero_retry_budget_is_attempted_exactly_once() -> None:
    governed = always_failing(budget=0)
    governed.run()

    assert governed.task("flaky").attempts == 1
    assert not any(e.type is EventType.RETRY_ATTEMPTED for e in governed.events)
    assert governed.run_state is not RunState.SUCCEEDED


def test_a_non_transient_failure_skips_retries_entirely() -> None:
    """Retrying a deterministic failure burns budget without any chance of success."""
    failing = task("hard_fail", retry_budget=3, produces=("out",))
    governed = engine(
        failing,
        executor=ScriptedFailureExecutor(DeterministicExecutor(), persistent=frozenset({"hard_fail"})),
    )
    governed.run()

    assert governed.task("hard_fail").attempts == 1
    assert governed.metrics.retry_count == 0
    assert governed.task("hard_fail").state is not TaskState.SUCCEEDED
    assert governed.run_state is not RunState.SUCCEEDED


def test_retry_budget_control_is_load_bearing() -> None:
    """Failures inside the budget must recover and succeed.

    Without this, ``test_retry_exhaustion_safe_stops_and_never_claims_success``
    would pass equally against an engine that never retries anything.
    """
    recovering = task("flaky", retry_budget=2, produces=("out",))
    governed = engine(
        recovering,
        executor=ScriptedFailureExecutor(DeterministicExecutor(), failures={"flaky": 2}),
    )

    assert governed.run() is RunState.SUCCEEDED
    assert governed.task("flaky").state is TaskState.SUCCEEDED
    assert governed.task("flaky").attempts == 3
    assert governed.metrics.retry_count == 2


def test_compensation_runs_before_the_safe_stop() -> None:
    """A terminal failure must invoke the named compensating action."""
    calls: list[tuple[str, str]] = []
    failing = task("flaky", retry_budget=1, produces=("out",), compensation="restore_snapshot")
    governed = engine(
        failing,
        executor=ScriptedFailureExecutor(DeterministicExecutor(), failures={"flaky": 99}),
        recovery_controller=RecoveryController(
            compensations={"restore_snapshot": lambda t, reason: calls.append((t.id, reason)) or True}
        ),
    )
    governed.run()

    assert calls, "the named compensating action was never invoked"
    types = [e.type for e in governed.events]
    assert EventType.COMPENSATION_EXECUTED in types
    assert types.index(EventType.COMPENSATION_EXECUTED) < types.index(EventType.SAFE_STOP_TRIGGERED)
    assert governed.metrics.rollback_count == 1


def test_metrics_are_reproducible_from_the_event_stream_alone() -> None:
    """Metrics must be a projection, not a parallel tally that can drift."""
    governed = always_failing(budget=2)
    governed.run()

    projected = compute_run_metrics(governed.events, run_id="run-negative")
    assert projected == governed.metrics


# ==========================================================================
# 6. An unsafe URL is denied while permitted work continues
#
# AGENTS.md lane C: "URL safety (scheme allowlist, no javascript:/data:)".
# AGENTS.md rule 6: "an unevaluated policy is a denial, not a pass."
# SCENARIO_ACCEPTANCE S-01: "A policy check rejects an intentionally unsafe URL
# fixture while permitted work continues correctly."
# ==========================================================================


PROHIBITED_DESTINATIONS = [
    "javascript:alert(1)",
    "JavaScript:alert(1)",  # scheme matching must be case-insensitive
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "file:///etc/passwd",
    "ftp://example.com/payload",
    "not-a-url",
    "",
]


@pytest.mark.parametrize("destination", PROHIBITED_DESTINATIONS)
def test_url_safety_policy_denies_a_prohibited_destination(destination: str) -> None:
    decision = PolicyEngine(clock=FixedClock()).evaluate_url("shorten", destination)
    assert decision.denied, f"{destination!r} must be denied"
    assert decision.reason, "a denial without a reason is not auditable"
    assert decision.policy_id and decision.task_id == "shorten"


@pytest.mark.parametrize("destination", ["https://example.com/ok", "http://example.com/ok"])
def test_url_safety_control_is_load_bearing(destination: str) -> None:
    """Allowed schemes must pass, or the policy is just a blanket denial."""
    decision = PolicyEngine(clock=FixedClock()).evaluate_url("shorten", destination)
    assert not decision.denied, f"{destination!r} must be permitted"


def test_an_unknown_policy_id_fails_closed() -> None:
    decision = PolicyEngine(clock=FixedClock()).evaluate("no-such-policy", "shorten", {})
    assert decision.denied, "an unrecognised policy must deny rather than default-allow"


def test_a_required_policy_that_was_never_evaluated_blocks_the_task() -> None:
    """Rule 6: an unevaluated policy is a denial, not a pass."""
    governed = engine(task("shorten", produces=("link",)),
                      required_policies={"shorten": ["url-safety"]})

    assert governed.run() is not RunState.SUCCEEDED
    assert governed.task("shorten").state is TaskState.BLOCKED
    assert not governed.artifacts


def test_a_denied_task_is_blocked_while_permitted_work_completes() -> None:
    """Containment, not a global halt: the safe sibling still has to finish."""
    policies = PolicyEngine(clock=FixedClock())
    governed = engine(
        task("shorten_unsafe", produces=("unsafe_link",)),
        task("shorten_safe", produces=("safe_link",)),
        required_policies={"shorten_unsafe": ["url-safety"], "shorten_safe": ["url-safety"]},
    )
    governed.start()
    governed.record_policy_decision(policies.evaluate_url("shorten_unsafe", "javascript:alert(1)"))
    governed.record_policy_decision(policies.evaluate_url("shorten_safe", "https://example.com/ok"))
    governed.run()

    assert governed.task("shorten_unsafe").state is TaskState.BLOCKED
    assert governed.task("shorten_safe").state is TaskState.SUCCEEDED, (
        "a policy denial on one task must not take down permitted work"
    )
    assert {a.name for a in governed.artifacts} == {"safe_link"}
    assert governed.metrics.policy_denials == 1


def test_policy_denial_control_is_load_bearing() -> None:
    """With a permitted destination on both tasks, neither is blocked."""
    policies = PolicyEngine(clock=FixedClock())
    governed = engine(
        task("shorten_unsafe", produces=("unsafe_link",)),
        task("shorten_safe", produces=("safe_link",)),
        required_policies={"shorten_unsafe": ["url-safety"], "shorten_safe": ["url-safety"]},
    )
    governed.start()
    governed.record_policy_decision(policies.evaluate_url("shorten_unsafe", "https://example.com/a"))
    governed.record_policy_decision(policies.evaluate_url("shorten_safe", "https://example.com/b"))

    assert governed.run() is RunState.SUCCEEDED
    assert governed.metrics.policy_denials == 0


def test_privacy_policy_denies_retaining_raw_client_identifiers() -> None:
    """The S-03 v2 requirement: no raw client identifiers retained."""
    policies = PolicyEngine(clock=FixedClock())
    denied = policies.evaluate_privacy("analytics", ["ip_address"], raw_client_identifiers_retained=True)
    allowed = policies.evaluate_privacy("analytics", ["country"], raw_client_identifiers_retained=False)

    assert denied.denied
    assert not allowed.denied, "a coarse derived dimension must remain permitted"


def test_change_control_denies_an_understated_breaking_change() -> None:
    """A breaking change declared as low impact would skip the approval gate."""
    policies = PolicyEngine(clock=FixedClock())
    understated = policies.evaluate_change("migrate", is_breaking=True, declared_impact=ImpactClass.LOW)
    honest = policies.evaluate_change("migrate", is_breaking=True, declared_impact=ImpactClass.BREAKING)

    assert understated.denied
    assert not honest.denied


# ==========================================================================
# 7. A re-plan preserves v1 history rather than overwriting it
#
# contracts.py: "Events are append-only. Event is immutable. History is never
# rewritten - re-planning adds records, it does not delete them."
# ACCEPTANCE_STRATEGY.md rank 7 failure signal: "Mutating the plan in place or
# continuing with stale outputs."
# ==========================================================================


def test_replan_appends_to_history_and_never_rewrites_it() -> None:
    producer = task("producer", produces=("spec",), stage=Stage.DESIGN)
    consumer = task("consumer", depends_on=("producer",), consumes=("spec",), produces=("code",))
    governed = engine(producer, consumer)
    governed.start()
    governed.promote_ready_tasks()
    governed.execute_task("producer")
    before = event_snapshot(governed)

    governed.replan(context(2, supersedes=1, change_reason="upstream requirement changed"),
                    changed_task_ids=["producer"])
    after = event_snapshot(governed)

    assert after[: len(before)] == before, "re-planning rewrote or dropped prior events"
    assert len(after) > len(before), "the re-plan left no trace of its own"
    assert [e["seq"] for e in after] == list(range(len(after))), "sequence numbers must stay contiguous"
    assert any(e["type"] == EventType.PLAN_REVISED.value for e in after[len(before):])


def test_replan_retains_the_superseded_plan_revision() -> None:
    governed, revised = replanned_pipeline()

    revisions = {p.revision: p for p in governed.plans}
    assert set(revisions) == {1, 2}, "the superseded plan revision was not retained"
    assert revisions[1].context_version == 1
    assert revisions[1].supersedes is None
    assert revisions[2].supersedes == 1
    assert revisions[2].context_version == revised.version
    assert governed.current_plan.revision == 2
    assert {t.id for t in revisions[1].tasks} == {t.id for t in revisions[2].tasks}


def test_replan_invalidates_only_affected_descendants() -> None:
    """Selective invalidation: unaffected work must be visibly retained.

    All three tasks have succeeded before the re-plan, so "retained stayed
    SUCCEEDED" is a real observation rather than a task that had not run yet.
    """
    governed = executed_pipeline(run_consumer=True)
    governed.replan(context(2, supersedes=1, change_reason="upstream requirement changed"),
                    changed_task_ids=["producer"])
    current = states(governed)
    stale_by_name = {a.name: a.stale for a in governed.artifacts}

    assert current["retained"] is TaskState.SUCCEEDED, (
        "work outside the changed lineage was discarded instead of retained"
    )
    assert current["producer"] is not TaskState.SUCCEEDED
    assert current["consumer"] is not TaskState.SUCCEEDED, (
        "a descendant of the changed task kept its result"
    )
    assert stale_by_name["spec"] is True
    assert stale_by_name["code"] is True, "the descendant's output was not invalidated"
    assert stale_by_name["docs"] is False, "unrelated output was invalidated anyway"
    assert any(e.type is EventType.TASK_INVALIDATED for e in governed.events)


def test_selective_invalidation_control_is_load_bearing() -> None:
    """Seed the re-plan on the other branch and the blast radius must move.

    Together with the test above this pins invalidation to the changed lineage
    rather than to task identity or to a blanket reset: seeding on ``producer``
    invalidates producer+consumer and retains ``retained``; seeding on
    ``retained`` must do exactly the opposite. If both seeds produce the same
    outcome, the descendant walk is not driving anything.
    """
    governed = executed_pipeline(run_consumer=True)
    governed.replan(context(2, supersedes=1, change_reason="documentation-only clarification"),
                    changed_task_ids=["retained"])
    current = states(governed)
    stale_by_name = {a.name: a.stale for a in governed.artifacts}

    assert current["retained"] is not TaskState.SUCCEEDED
    assert current["producer"] is TaskState.SUCCEEDED, (
        "an unrelated branch was invalidated, so invalidation is not selective"
    )
    assert current["consumer"] is TaskState.SUCCEEDED
    assert stale_by_name["docs"] is True
    assert stale_by_name["spec"] is False
    assert stale_by_name["code"] is False


def test_replan_is_recorded_as_an_attributable_decision() -> None:
    governed, _ = replanned_pipeline()
    triggers = [e for e in governed.events if e.type is EventType.REPLAN_TRIGGERED]

    assert triggers, "the re-plan was not recorded in the event stream"
    assert any(e.type is EventType.CONTEXT_VERSION_CREATED for e in governed.events)
    assert governed.metrics.replans == 1


def test_a_context_version_cannot_supersede_itself_or_a_later_version() -> None:
    """v1 history stays reachable only if the version chain runs strictly backwards."""
    with pytest.raises(ValidationError):
        context(2, supersedes=2)
    with pytest.raises(ValidationError):
        context(2, supersedes=3)
    assert context(2, supersedes=1).supersedes == 1  # control


def test_an_event_cannot_be_rewritten_after_it_is_recorded() -> None:
    record = Event(run_id="run-negative", seq=0, type=EventType.RUN_STARTED, at=T0)
    with pytest.raises(ValidationError):
        record.summary = "a more flattering account"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        record.type = EventType.RUN_COMPLETED  # type: ignore[misc]


@pytest.mark.parametrize("seq", [0, 5, 99])
def test_event_store_refuses_a_replayed_or_skipped_sequence(seq: int) -> None:
    """Append-only means exactly next: no overwrite, no gap to backfill later."""
    store = EventStore()
    store.append(run_id="run-negative", type=EventType.RUN_STARTED, at=T0)

    with pytest.raises(EventStoreError):
        store.append_event(
            Event(run_id="run-negative", seq=seq, type=EventType.RUN_COMPLETED, at=T1)
        )
    assert len(store.events("run-negative")) == 1


def test_event_store_append_control_is_load_bearing() -> None:
    """The next sequence number must be accepted, or nothing could ever be written."""
    store = EventStore()
    first = store.append(run_id="run-negative", type=EventType.RUN_STARTED, at=T0)
    second = store.append_event(
        Event(run_id="run-negative", seq=first.seq + 1, type=EventType.RUN_COMPLETED, at=T1)
    )

    assert second.seq == first.seq + 1
    assert len(store.events("run-negative")) == 2
