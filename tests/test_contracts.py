"""Locks the frozen contracts.

These tests exist so that components built in parallel against this file cannot
silently diverge, and so that the three invariants the contracts claim are
actually enforced rather than documented. If a change here fails, it is a
breaking change to every downstream module - which is exactly the signal wanted.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from orchestrator.clock import FixedClock, SeededIdGen, deterministic_pair
from orchestrator.contracts import (
    Actor,
    ActorKind,
    Ambiguity,
    Approval,
    Artifact,
    ContextVersion,
    Event,
    EventType,
    Gate,
    GateKind,
    ImpactClass,
    LEGAL_RUN_TRANSITIONS,
    LEGAL_TASK_TRANSITIONS,
    Plan,
    PolicyDecision,
    PolicyEffect,
    RunState,
    Stage,
    Task,
    TaskState,
    TERMINAL_RUN_STATES,
    is_legal_run_transition,
    is_legal_task_transition,
)
from orchestrator.executor import (
    DeterministicExecutor,
    ScriptedFailureExecutor,
    TaskFailure,
    TaskOutput,
)

HUMAN = Actor(kind=ActorKind.HUMAN, id="reviewer:alex")
AGENT = Actor(kind=ActorKind.AGENT, id="agent:planner")


# --------------------------------------------------------------------------
# Invariant 1: events are append-only
# --------------------------------------------------------------------------


def test_events_are_immutable():
    event = Event(run_id="run-0001", seq=0, type=EventType.RUN_STARTED, at="2026-01-01T00:00:00+00:00")
    with pytest.raises(ValidationError):
        event.seq = 5


def test_event_round_trips_through_json():
    """Evidence bundles are JSON on disk; the schema must survive the trip."""
    event = Event(
        run_id="run-0001",
        seq=7,
        type=EventType.POLICY_EVALUATED,
        at="2026-01-01T00:00:07+00:00",
        actor=AGENT,
        task_id="task-0003",
        summary="unsafe destination rejected",
        payload={"policy_id": "url-safety", "effect": "deny"},
    )
    restored = Event.model_validate_json(event.model_dump_json())
    assert restored == event


# --------------------------------------------------------------------------
# Invariant 2: transitions are explicit and fail closed
# --------------------------------------------------------------------------


def test_every_task_state_has_a_transition_entry():
    """A missing entry would silently deny all moves out of that state."""
    for state in TaskState:
        assert state in LEGAL_TASK_TRANSITIONS


def test_every_run_state_has_a_transition_entry():
    for state in RunState:
        assert state in LEGAL_RUN_TRANSITIONS


def test_illegal_task_transition_is_rejected():
    assert is_legal_task_transition(TaskState.PENDING, TaskState.READY)
    # Skipping straight from PENDING to SUCCEEDED would bypass every gate.
    assert not is_legal_task_transition(TaskState.PENDING, TaskState.SUCCEEDED)
    assert not is_legal_task_transition(TaskState.SUCCEEDED, TaskState.RUNNING)


def test_terminal_run_states_have_no_exits():
    for state in TERMINAL_RUN_STATES:
        assert LEGAL_RUN_TRANSITIONS[state] == frozenset()
        assert not is_legal_run_transition(state, RunState.RUNNING)


def test_replan_can_reclaim_a_succeeded_task():
    """Selective invalidation depends on SUCCEEDED -> STALE -> PENDING."""
    assert is_legal_task_transition(TaskState.SUCCEEDED, TaskState.STALE)
    assert is_legal_task_transition(TaskState.STALE, TaskState.PENDING)


# --------------------------------------------------------------------------
# Invariant 3: approval is human-owned
# --------------------------------------------------------------------------


def test_agent_cannot_grant_approval():
    with pytest.raises(ValidationError, match="human actor"):
        Approval(
            id="apr-0001",
            task_id="task-0009",
            granted=True,
            actor=AGENT,
            rationale="self-approved",
            decided_at="2026-01-01T00:00:00+00:00",
        )


def test_human_approval_is_accepted():
    approval = Approval(
        id="apr-0001",
        task_id="task-0009",
        granted=True,
        actor=HUMAN,
        rationale="breaking contract change reviewed",
        decided_at="2026-01-01T00:00:00+00:00",
    )
    assert approval.granted and approval.actor.kind is ActorKind.HUMAN


@pytest.mark.parametrize(
    "impact,expected",
    [
        (ImpactClass.LOW, False),
        (ImpactClass.MEDIUM, False),
        (ImpactClass.HIGH, True),
        (ImpactClass.BREAKING, True),
    ],
)
def test_high_impact_work_requires_approval(impact, expected):
    task = Task(id="task-0001", name="migrate schema", stage=Stage.IMPLEMENTATION,
                capability="engineer", impact=impact)
    assert task.requires_approval is expected


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_fixed_clock_is_monotonic_and_reproducible():
    a = FixedClock(step=timedelta(seconds=5))
    b = FixedClock(step=timedelta(seconds=5))
    assert [a.iso() for _ in range(3)] == [b.iso() for _ in range(3)]

    clock = FixedClock(step=timedelta(seconds=5))
    first, second = clock.now(), clock.now()
    assert second - first == timedelta(seconds=5)
    assert clock.peek() == second + timedelta(seconds=5)  # peek does not consume
    assert clock.peek() == clock.now()


def test_seeded_ids_are_stable_and_namespaced():
    gen = SeededIdGen()
    assert gen.next_id("task") == "task-0001"
    assert gen.next_id("task") == "task-0002"
    assert gen.next_id("run") == "run-0001"


def test_deterministic_pair_reproduces_across_runs():
    def sample():
        clock, ids = deterministic_pair()
        return [(clock.iso(), ids.next_id("task")) for _ in range(4)]

    assert sample() == sample()


# --------------------------------------------------------------------------
# Lineage and versioning
# --------------------------------------------------------------------------


def test_context_version_must_supersede_an_earlier_version():
    with pytest.raises(ValidationError, match="earlier version"):
        ContextVersion(
            version=1,
            raw_requirement="improve link analytics",
            normalized_problem="...",
            supersedes=1,
            created_at="2026-01-01T00:00:00+00:00",
        )


def test_context_version_two_retains_lineage():
    v2 = ContextVersion(
        version=2,
        raw_requirement="improve link analytics",
        normalized_problem="coarse, non-identifying analytics only",
        ambiguities=(
            Ambiguity(
                id="amb-0001",
                question="may raw client identifiers be stored?",
                proposed_assumption="no raw IP or user agent is retained",
                consequence_if_wrong="analytics granularity is lower than expected",
            ),
        ),
        supersedes=1,
        change_reason="privacy clarification from stakeholder",
        created_at="2026-01-01T00:01:00+00:00",
    )
    assert v2.supersedes == 1 and v2.change_reason


def test_artifact_staleness_is_the_only_mutable_field():
    artifact = Artifact(
        id="art-0001", name="api_contract", version=1, produced_by_task="task-0002",
        context_version=1, content_hash="abc123", created_at="2026-01-01T00:00:00+00:00",
    )
    assert artifact.stale is False
    artifact.stale = True  # a re-plan flips this, always alongside an event
    assert artifact.stale is True


def test_task_retry_accounting():
    task = Task(id="task-0001", name="build", stage=Stage.IMPLEMENTATION,
                capability="engineer", retry_budget=2)
    assert task.retries_remaining == 2
    assert task.model_copy(update={"attempts": 1}).retries_remaining == 2  # first try isn't a retry
    assert task.model_copy(update={"attempts": 3}).retries_remaining == 0


def test_plan_revision_supersedes_without_mutation():
    task = Task(id="task-0001", name="normalize", stage=Stage.REQUIREMENTS, capability="analyst")
    v1 = Plan(revision=1, context_version=1, tasks=(task,), created_at="2026-01-01T00:00:00+00:00")
    v2 = Plan(revision=2, context_version=2, tasks=(task,), supersedes=1,
              change_reason="privacy clarification", created_at="2026-01-01T00:02:00+00:00")
    assert v1.revision == 1 and v2.supersedes == 1
    assert v1.task_by_id("task-0001") is not None
    assert v1.task_by_id("nope") is None


# --------------------------------------------------------------------------
# Gates and policy
# --------------------------------------------------------------------------


def test_gates_and_policy_decisions_are_recorded_both_ways():
    gate = Gate(id="gate-entry-impact", kind=GateKind.ENTRY, description="impact analysis exists")
    assert gate.kind is GateKind.ENTRY

    allow = PolicyDecision(policy_id="url-safety", task_id="task-0003",
                           effect=PolicyEffect.ALLOW, reason="destination on allowlist scheme",
                           evaluated_at="2026-01-01T00:00:00+00:00")
    deny = PolicyDecision(policy_id="url-safety", task_id="task-0004",
                          effect=PolicyEffect.DENY, reason="javascript: scheme rejected",
                          evaluated_at="2026-01-01T00:00:01+00:00")
    assert not allow.denied and deny.denied


# --------------------------------------------------------------------------
# Executor seam
# --------------------------------------------------------------------------


def test_deterministic_executor_stubs_declared_outputs():
    task = Task(id="task-0001", name="write docs", stage=Stage.DOCUMENTATION,
                capability="tech-writer", produces=("readme",))
    output = DeterministicExecutor().execute(task, {})
    assert output.succeeded and set(output.artifacts) == {"readme"}


def test_registered_handler_overrides_the_stub():
    task = Task(id="task-0001", name="write docs", stage=Stage.DOCUMENTATION,
                capability="tech-writer", produces=("readme",))
    executor = DeterministicExecutor()
    executor.register("task-0001", lambda t, i: TaskOutput(artifacts={"readme": "# real"}))
    assert executor.execute(task, {}).artifacts["readme"] == "# real"


def test_transient_failure_clears_after_scripted_attempts():
    task = Task(id="task-0001", name="flaky", stage=Stage.TESTING, capability="qa")
    executor = ScriptedFailureExecutor(DeterministicExecutor(), failures={"task-0001": 2})

    for _ in range(2):
        with pytest.raises(TaskFailure) as exc:
            executor.execute(task, {})
        assert exc.value.transient
    assert executor.execute(task, {}).succeeded


def test_persistent_failure_never_clears_and_is_marked_non_transient():
    task = Task(id="task-0001", name="doomed", stage=Stage.TESTING, capability="qa")
    executor = ScriptedFailureExecutor(DeterministicExecutor(), persistent=frozenset({"task-0001"}))
    for _ in range(3):
        with pytest.raises(TaskFailure) as exc:
            executor.execute(task, {})
        assert not exc.value.transient
