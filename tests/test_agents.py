"""Executable checks for capability-owned agent dispatch and attribution."""

from __future__ import annotations

import pytest

from orchestrator.agents import Agent, AgentDispatchError, AgentRegistry
from orchestrator.clock import deterministic_pair
from orchestrator.contracts import Actor, ActorKind, EventType, Plan, RunState, Stage, Task
from orchestrator.engine import OrchestrationEngine
from orchestrator.executor import TaskOutput


def _task(task_id: str, capability: str) -> Task:
    return Task(
        id=task_id,
        name=task_id,
        stage=Stage.TESTING,
        capability=capability,
        produces=(f"{task_id}-output",),
    )


def test_registry_dispatches_by_capability_then_task_id() -> None:
    calls: list[str] = []

    def handler(label: str):
        def execute(task: Task, _inputs: dict[str, str]) -> TaskOutput:
            calls.append(label)
            return TaskOutput(
                summary=label,
                artifacts={task.produces[0]: label},
            )

        return execute

    registry = AgentRegistry()
    registry.register(
        Agent(
            "agent:quality-engineer",
            "quality-engineer",
            {
                "red-test": handler("quality:red"),
                "green-test": handler("quality:green"),
            },
        )
    )
    registry.register(
        Agent(
            "agent:backend-engineer",
            "backend-engineer",
            {"red-test": handler("backend:red")},
        )
    )

    assert registry.execute(_task("red-test", "quality-engineer"), {}).summary == "quality:red"
    assert registry.execute(_task("green-test", "quality-engineer"), {}).summary == "quality:green"
    assert registry.execute(_task("red-test", "backend-engineer"), {}).summary == "backend:red"
    assert calls == ["quality:red", "quality:green", "backend:red"]


def test_registry_fails_closed_for_an_unregistered_capability() -> None:
    registry = AgentRegistry()
    registry.register(Agent("agent:quality-engineer", "quality-engineer", {}))

    with pytest.raises(AgentDispatchError, match="no agent registered.*quality-enginer"):
        registry.execute(_task("red-test", "quality-enginer"), {})


def test_registry_fails_closed_when_the_agent_has_no_task_handler() -> None:
    registry = AgentRegistry()
    registry.register(Agent("agent:quality-engineer", "quality-engineer", {}))

    with pytest.raises(AgentDispatchError, match="no handler.*red-test"):
        registry.execute(_task("red-test", "quality-engineer"), {})


def test_engine_attributes_task_events_to_the_dispatched_agent() -> None:
    clock, ids = deterministic_pair()
    task = _task("validate", "quality-engineer")
    registry = AgentRegistry()
    registry.register(
        Agent(
            "agent:quality-engineer",
            "quality-engineer",
            {
                "validate": lambda task, _inputs: TaskOutput(
                    summary="validation complete",
                    artifacts={task.produces[0]: "green"},
                )
            },
        )
    )
    orchestrator = Actor(kind=ActorKind.AGENT, id="agent:scenario-orchestrator")
    engine = OrchestrationEngine(
        Plan(context_version=1, tasks=(task,), created_at=clock.iso()),
        registry,
        clock=clock,
        id_gen=ids,
    )

    assert engine.run(actor=orchestrator) is RunState.SUCCEEDED
    task_events = [event for event in engine.events if event.task_id == task.id]
    assert task_events
    assert {event.actor.id for event in task_events} == {"agent:quality-engineer"}
    run_started = next(event for event in engine.events if event.type is EventType.RUN_STARTED)
    assert run_started.actor == orchestrator


def test_engine_rejects_a_plan_capability_without_a_registered_agent() -> None:
    clock, ids = deterministic_pair()
    task = _task("validate", "quality-enginer")
    registry = AgentRegistry()
    registry.register(Agent("agent:quality-engineer", "quality-engineer", {}))
    engine = OrchestrationEngine(
        Plan(context_version=1, tasks=(task,), created_at=clock.iso()),
        registry,
        clock=clock,
        id_gen=ids,
    )

    with pytest.raises(AgentDispatchError, match="no agent registered.*quality-enginer"):
        engine.run()
