"""Validated dependency-graph operations for orchestration plans.

The graph is intentionally free of execution and governance decisions.  It
answers structural questions -- whether a plan is a DAG, which tasks are ready,
and which tasks descend from a changed node -- while :mod:`orchestrator.engine`
owns state transitions, gates, and events.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping

from orchestrator.contracts import GateKind, Plan, Task, TaskState


class GraphValidationError(ValueError):
    """Raised when a plan cannot be represented as a safe dependency DAG."""


class DependencyGraph:
    """A deterministic, validated view over the tasks in a :class:`Plan`.

    Task declaration order is retained as the stable tie-breaker for
    topological traversal.  This makes scenario event streams reproducible even
    when several nodes become ready at the same time.
    """

    def __init__(self, plan: Plan) -> None:
        self._tasks = tuple(plan.tasks)
        self._order = {task.id: index for index, task in enumerate(self._tasks)}
        self._by_id: dict[str, Task] = {}
        self._children: dict[str, list[str]] = {}
        self._artifact_producers: dict[str, str] = {}
        self._validate_and_index()
        self._topological_ids = self._build_topological_order()

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(task.id for task in self._tasks)

    @property
    def artifact_producers(self) -> Mapping[str, str]:
        return dict(self._artifact_producers)

    def task(self, task_id: str) -> Task:
        try:
            return self._by_id[task_id]
        except KeyError as exc:
            raise KeyError(f"unknown task '{task_id}'") from exc

    def topological_order(self) -> tuple[Task, ...]:
        """Return all tasks in stable topological order."""
        return tuple(self._by_id[task_id] for task_id in self._topological_ids)

    def ancestors(self, task_id: str) -> frozenset[str]:
        """Return every direct and transitive dependency of ``task_id``."""
        self.task(task_id)
        found: set[str] = set()
        pending = list(self._by_id[task_id].depends_on)
        while pending:
            current = pending.pop()
            if current in found:
                continue
            found.add(current)
            pending.extend(self._by_id[current].depends_on)
        return frozenset(found)

    def descendants(self, task_ids: Iterable[str]) -> frozenset[str]:
        """Return the supplied tasks and all nodes causally downstream."""
        seeds = tuple(task_ids)
        unknown = sorted(set(seeds) - self._by_id.keys())
        if unknown:
            raise KeyError(f"unknown task(s): {', '.join(unknown)}")

        found: set[str] = set(seeds)
        pending: deque[str] = deque(seeds)
        while pending:
            current = pending.popleft()
            for child in self._children[current]:
                if child not in found:
                    found.add(child)
                    pending.append(child)
        return frozenset(found)

    def dependencies_satisfied(self, task_id: str) -> bool:
        """Whether every declared dependency has succeeded."""
        task = self.task(task_id)
        return all(
            self._by_id[dependency].state is TaskState.SUCCEEDED
            for dependency in task.depends_on
        )

    def ready_tasks(self) -> tuple[Task, ...]:
        """Pending tasks whose dependency states allow them to become READY."""
        return tuple(
            task
            for task in self.topological_order()
            if task.state is TaskState.PENDING and self.dependencies_satisfied(task.id)
        )

    def blocked_by_terminal_dependency(self) -> tuple[Task, ...]:
        """Pending tasks that can never run because an ancestor did not succeed."""
        terminal_non_success = {
            TaskState.FAILED,
            TaskState.COMPENSATED,
            TaskState.BLOCKED,
            TaskState.STALE,
            TaskState.SKIPPED,
        }
        return tuple(
            task
            for task in self.topological_order()
            if task.state is TaskState.PENDING
            and any(
                self._by_id[dependency].state in terminal_non_success
                for dependency in task.depends_on
            )
        )

    def producer_for(self, artifact_name: str) -> Task | None:
        task_id = self._artifact_producers.get(artifact_name)
        return self._by_id[task_id] if task_id is not None else None

    def _validate_and_index(self) -> None:
        for task in self._tasks:
            if not task.id.strip():
                raise GraphValidationError("task ids must not be empty")
            if task.id in self._by_id:
                raise GraphValidationError(f"duplicate task id '{task.id}'")
            self._by_id[task.id] = task
            self._children[task.id] = []

        for task in self._tasks:
            if len(set(task.depends_on)) != len(task.depends_on):
                raise GraphValidationError(f"task '{task.id}' repeats a dependency")
            for dependency in task.depends_on:
                if dependency == task.id:
                    raise GraphValidationError(f"task '{task.id}' depends on itself")
                if dependency not in self._by_id:
                    raise GraphValidationError(
                        f"task '{task.id}' depends on unknown task '{dependency}'"
                    )
                self._children[dependency].append(task.id)

            for gate in task.entry_gates:
                if gate.kind is not GateKind.ENTRY:
                    raise GraphValidationError(
                        f"task '{task.id}' has non-entry gate '{gate.id}' in entry_gates"
                    )
            for gate in task.exit_gates:
                if gate.kind is not GateKind.EXIT:
                    raise GraphValidationError(
                        f"task '{task.id}' has non-exit gate '{gate.id}' in exit_gates"
                    )

            if len(set(task.produces)) != len(task.produces):
                raise GraphValidationError(f"task '{task.id}' repeats a produced artifact")
            for artifact_name in task.produces:
                existing = self._artifact_producers.get(artifact_name)
                if existing is not None:
                    raise GraphValidationError(
                        f"artifact '{artifact_name}' has multiple producers: "
                        f"'{existing}' and '{task.id}'"
                    )
                self._artifact_producers[artifact_name] = task.id

        # Cycle detection must precede ancestor checks below.
        self._build_topological_order()

        for task in self._tasks:
            ancestor_ids = self.ancestors(task.id)
            for artifact_name in task.consumes:
                producer_id = self._artifact_producers.get(artifact_name)
                if producer_id is None:
                    raise GraphValidationError(
                        f"task '{task.id}' consumes unproduced artifact '{artifact_name}'"
                    )
                if producer_id not in ancestor_ids:
                    raise GraphValidationError(
                        f"task '{task.id}' consumes '{artifact_name}' from '{producer_id}', "
                        "but that producer is not a dependency ancestor"
                    )

    def _build_topological_order(self) -> tuple[str, ...]:
        indegree = {task.id: len(task.depends_on) for task in self._tasks}
        available = [task.id for task in self._tasks if indegree[task.id] == 0]
        available.sort(key=self._order.__getitem__)
        result: list[str] = []

        while available:
            current = available.pop(0)
            result.append(current)
            for child in sorted(self._children[current], key=self._order.__getitem__):
                indegree[child] -= 1
                if indegree[child] == 0:
                    available.append(child)
                    available.sort(key=self._order.__getitem__)

        if len(result) != len(self._tasks):
            cyclic = [task_id for task_id, degree in indegree.items() if degree > 0]
            raise GraphValidationError(
                "dependency cycle detected involving: " + ", ".join(cyclic)
            )
        return tuple(result)
