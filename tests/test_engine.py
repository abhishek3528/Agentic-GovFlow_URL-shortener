from __future__ import annotations

import pytest

from orchestrator.clock import deterministic_pair
from orchestrator.contracts import (
    ContextVersion,
    EventType,
    Gate,
    GateKind,
    Plan,
    RunState,
    Stage,
    Task,
    TaskState,
)
from orchestrator.engine import EngineError, IllegalTransitionError, OrchestrationEngine
from orchestrator.executor import DeterministicExecutor, TaskOutput


def fork_join_plan() -> Plan:
    exit_gate = Gate(id="output-reviewed", kind=GateKind.EXIT, description="output reviewed")
    root = Task(
        id="root",
        name="normalize requirement",
        stage=Stage.REQUIREMENTS,
        capability="analyst",
        produces=("requirement",),
        exit_gates=(exit_gate,),
    )
    api = Task(
        id="api",
        name="build API",
        stage=Stage.IMPLEMENTATION,
        capability="engineer",
        depends_on=("root",),
        consumes=("requirement",),
        produces=("api_code",),
        exit_gates=(exit_gate,),
    )
    analytics = Task(
        id="analytics",
        name="build analytics",
        stage=Stage.IMPLEMENTATION,
        capability="engineer",
        depends_on=("root",),
        consumes=("requirement",),
        produces=("analytics_code",),
        exit_gates=(exit_gate,),
    )
    join = Task(
        id="join",
        name="integrated validation",
        stage=Stage.TESTING,
        capability="qa",
        depends_on=("api", "analytics"),
        consumes=("api_code", "analytics_code"),
        produces=("test_report",),
        exit_gates=(exit_gate,),
    )
    return Plan(
        context_version=1,
        tasks=(root, api, analytics, join),
        created_at="2026-01-01T00:00:00+00:00",
    )


def always_pass(gate, task, artifacts):
    return True, "evidence present"


def test_fork_join_executes_end_to_end_with_ordered_events():
    clock, ids = deterministic_pair()
    engine = OrchestrationEngine(
        fork_join_plan(),
        DeterministicExecutor(),
        clock=clock,
        id_gen=ids,
        gate_evaluator=always_pass,
    )

    assert engine.run() is RunState.SUCCEEDED
    assert all(
        task.state is TaskState.SUCCEEDED for task in engine.current_plan.tasks
    )
    assert [event.seq for event in engine.events] == list(range(len(engine.events)))

    transitions = [
        event
        for event in engine.events
        if event.type is EventType.TASK_STATE_CHANGED
    ]

    def event_index(task_id, destination):
        return next(
            index
            for index, event in enumerate(transitions)
            if event.task_id == task_id and event.payload["to"] == destination
        )

    # Both fork nodes become ready before either executes, and the join waits
    # for both successful branch outputs.
    assert event_index("api", "ready") < event_index("api", "running")
    assert event_index("analytics", "ready") < event_index("api", "running")
    assert event_index("join", "running") > event_index("api", "succeeded")
    assert event_index("join", "running") > event_index("analytics", "succeeded")

    artifacts = {artifact.name: artifact for artifact in engine.artifacts}
    assert set(artifacts["test_report"].derived_from) == {
        artifacts["api_code"].id,
        artifacts["analytics_code"].id,
    }


def test_illegal_transition_is_rejected_without_an_event():
    engine = OrchestrationEngine(fork_join_plan(), DeterministicExecutor())
    before = len(engine.events)
    with pytest.raises(IllegalTransitionError, match="pending -> succeeded"):
        engine.transition_task("root", TaskState.SUCCEEDED)
    assert engine.task("root").state is TaskState.PENDING
    assert len(engine.events) == before


def test_individually_legal_transition_cannot_bypass_governed_operations():
    engine = OrchestrationEngine(fork_join_plan(), DeterministicExecutor())
    engine.start()
    assert engine.promote_ready_tasks() == ("root",)
    before = len(engine.events)

    with pytest.raises(EngineError, match="direct task transitions"):
        engine.transition_task("root", TaskState.RUNNING)

    assert engine.task("root").state is TaskState.READY
    assert len(engine.events) == before


def test_missing_branch_output_blocks_join():
    executor = DeterministicExecutor(
        {
            "analytics": lambda task, inputs: TaskOutput(
                succeeded=True,
                summary="claimed success without its output",
                artifacts={},
            )
        }
    )
    engine = OrchestrationEngine(
        fork_join_plan(), executor, gate_evaluator=always_pass
    )

    assert engine.run() is RunState.FAILED
    assert engine.task("analytics").state is TaskState.BLOCKED
    assert engine.task("join").state is TaskState.SKIPPED
    assert not any(
        event.task_id == "join" and event.payload.get("to") == "running"
        for event in engine.events
    )


def test_declared_gate_without_evaluator_fails_closed():
    engine = OrchestrationEngine(fork_join_plan(), DeterministicExecutor())

    assert engine.run() is RunState.FAILED
    assert engine.task("root").state is TaskState.BLOCKED
    denial = next(
        event
        for event in engine.events
        if event.type is EventType.GATE_EVALUATED and event.task_id == "root"
    )
    assert denial.payload["passed"] is False
    assert "failed closed" in denial.payload["reason"]


def test_deterministic_execution_replays_byte_stable_structure():
    def replay():
        clock, ids = deterministic_pair()
        engine = OrchestrationEngine(
            fork_join_plan(),
            DeterministicExecutor(),
            clock=clock,
            id_gen=ids,
            gate_evaluator=always_pass,
        )
        engine.run()
        return (
            [event.model_dump_json() for event in engine.events],
            [artifact.model_dump_json() for artifact in engine.artifacts],
        )

    assert replay() == replay()


def test_replan_invalidates_only_changed_descendants_and_preserves_revision_one():
    root_a = Task(
        id="a",
        name="A",
        stage=Stage.DESIGN,
        capability="architect",
        produces=("a_output",),
    )
    root_b = Task(
        id="b",
        name="B",
        stage=Stage.DOCUMENTATION,
        capability="writer",
        produces=("b_output",),
    )
    join = Task(
        id="join",
        name="join",
        stage=Stage.TESTING,
        capability="qa",
        depends_on=("a", "b"),
        consumes=("a_output", "b_output"),
        produces=("joined",),
    )
    plan = Plan(
        revision=1,
        context_version=1,
        tasks=(root_a, root_b, join),
        created_at="2026-01-01T00:00:00+00:00",
    )
    engine = OrchestrationEngine(plan, DeterministicExecutor())
    engine.start()
    assert engine.promote_ready_tasks() == ("a", "b")
    for task_id in ("a", "b"):
        engine.execute_task(task_id)

    context_v2 = ContextVersion(
        version=2,
        raw_requirement="changed A only",
        normalized_problem="recompute A and its descendants",
        supersedes=1,
        change_reason="upstream A changed",
        created_at="2026-01-01T00:01:00+00:00",
    )
    revised = engine.replan(context_v2, changed_task_ids=("a",))

    assert revised.revision == 2 and revised.supersedes == 1
    assert engine.plans[0].revision == 1 and engine.plans[0].supersedes is None
    assert engine.task("a").state is TaskState.PENDING
    assert engine.task("join").state is TaskState.PENDING
    assert engine.task("b").state is TaskState.SUCCEEDED

    old_artifacts = {artifact.name: artifact for artifact in engine.artifacts}
    assert old_artifacts["a_output"].stale is True
    assert old_artifacts["b_output"].stale is False

    assert engine.run() is RunState.SUCCEEDED
    current = {artifact.name: artifact for artifact in engine.artifacts}
    assert current["a_output"].version == 2
    assert current["b_output"].version == 1
    assert any(event.type is EventType.PLAN_REVISED for event in engine.events)
