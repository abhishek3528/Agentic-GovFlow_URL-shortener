from __future__ import annotations

import pytest

from orchestrator.contracts import Plan, Stage, Task, TaskState
from orchestrator.graph import DependencyGraph, GraphValidationError


def make_plan(*tasks: Task) -> Plan:
    return Plan(
        context_version=1,
        tasks=tasks,
        created_at="2026-01-01T00:00:00+00:00",
    )


def test_topological_frontier_and_descendants_are_deterministic():
    root = Task(
        id="root",
        name="normalize",
        stage=Stage.REQUIREMENTS,
        capability="analyst",
        produces=("requirement",),
    )
    left = Task(
        id="left",
        name="implementation A",
        stage=Stage.IMPLEMENTATION,
        capability="engineer",
        depends_on=("root",),
        consumes=("requirement",),
        produces=("left_output",),
    )
    right = Task(
        id="right",
        name="implementation B",
        stage=Stage.IMPLEMENTATION,
        capability="engineer",
        depends_on=("root",),
        consumes=("requirement",),
        produces=("right_output",),
    )
    join = Task(
        id="join",
        name="integrate",
        stage=Stage.TESTING,
        capability="qa",
        depends_on=("left", "right"),
        consumes=("left_output", "right_output"),
    )
    graph = DependencyGraph(make_plan(root, left, right, join))

    assert [task.id for task in graph.topological_order()] == [
        "root",
        "left",
        "right",
        "join",
    ]
    assert [task.id for task in graph.ready_tasks()] == ["root"]
    root.state = TaskState.SUCCEEDED
    assert [task.id for task in graph.ready_tasks()] == ["left", "right"]
    assert graph.descendants(("left",)) == frozenset({"left", "join"})


def test_cycle_and_unknown_dependency_are_rejected():
    a = Task(
        id="a", name="a", stage=Stage.DESIGN, capability="architect", depends_on=("b",)
    )
    b = Task(
        id="b", name="b", stage=Stage.DESIGN, capability="architect", depends_on=("a",)
    )
    with pytest.raises(GraphValidationError, match="cycle"):
        DependencyGraph(make_plan(a, b))

    orphan = Task(
        id="orphan",
        name="orphan",
        stage=Stage.TESTING,
        capability="qa",
        depends_on=("missing",),
    )
    with pytest.raises(GraphValidationError, match="unknown task"):
        DependencyGraph(make_plan(orphan))


def test_consumed_artifact_must_come_from_a_dependency_ancestor():
    producer = Task(
        id="producer",
        name="producer",
        stage=Stage.DESIGN,
        capability="architect",
        produces=("contract",),
    )
    consumer = Task(
        id="consumer",
        name="consumer",
        stage=Stage.IMPLEMENTATION,
        capability="engineer",
        consumes=("contract",),
    )
    with pytest.raises(GraphValidationError, match="not a dependency ancestor"):
        DependencyGraph(make_plan(producer, consumer))

