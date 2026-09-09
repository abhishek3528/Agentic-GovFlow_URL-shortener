"""The seam between governance and the work itself.

The orchestrator's job is deciding *whether* a task may run, in what order, under
which gates, and what happens when it fails. It is deliberately ignorant of how
the work gets done. That separation is what makes this an orchestration engine
rather than a script: the same governed graph runs with deterministic executors
in CI and could run with model-backed executors without the control plane
changing.

``DeterministicExecutor`` is the direct credential-free implementation retained
for core and adversarial tests. ``AgentRegistry`` in ``orchestrator.agents`` is
the second implementation and drives the scenarios through capability-owned
roles. ``ScriptedFailureExecutor`` wraps another executor to inject transient or
persistent failures - this is how retry budgets, compensation and safe-stop are
exercised as real code paths rather than asserted in prose.
"""

from __future__ import annotations

from typing import Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.contracts import Actor, Task


class TaskOutput(BaseModel):
    """What an executor returns for one attempt at one task.

    ``artifacts`` maps a logical artifact name (as declared in ``Task.produces``)
    to its content. The engine, not the executor, assigns ids, versions, hashes
    and lineage - executors cannot forge provenance.
    """

    model_config = ConfigDict(frozen=True)

    succeeded: bool = True
    summary: str = ""
    artifacts: dict[str, str] = Field(default_factory=dict)
    validation_results: dict[str, bool] = Field(default_factory=dict)
    failure_reason: str | None = None


class TaskFailure(Exception):
    """Raised by an executor when an attempt fails.

    ``transient`` tells the engine whether retrying is meaningful. A persistent
    failure short-circuits the retry budget and goes straight to compensation or
    safe-stop, which is both more honest and faster than burning retries on a
    deterministic error.
    """

    def __init__(self, reason: str, *, transient: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.transient = transient


class TaskExecutor(Protocol):
    """Performs the work of a single task attempt.

    Implementations must not mutate engine state, write events, or decide
    governance outcomes. They do work and report a result; everything else is
    the engine's responsibility.
    """

    def execute(self, task: Task, inputs: dict[str, str]) -> TaskOutput: ...


Handler = Callable[[Task, dict[str, str]], TaskOutput]


class DeterministicExecutor:
    """Default credential-free executor.

    Work is provided as per-task handlers registered by the scenario. Any task
    without a registered handler produces a declared-output stub, so a plan can
    be executed end to end before every handler exists - useful while building,
    and honest in evidence because stub output is labelled as such.
    """

    def __init__(self, handlers: dict[str, Handler] | None = None) -> None:
        self._handlers: dict[str, Handler] = dict(handlers or {})

    def register(self, task_id: str, handler: Handler) -> None:
        self._handlers[task_id] = handler

    def execute(self, task: Task, inputs: dict[str, str]) -> TaskOutput:
        handler = self._handlers.get(task.id)
        if handler is not None:
            return handler(task, inputs)
        return TaskOutput(
            succeeded=True,
            summary=f"stub output for '{task.name}'",
            artifacts={name: f"<stub:{name}>" for name in task.produces},
        )


class ScriptedFailureExecutor:
    """Wraps an executor and injects failures on a scripted schedule.

    ``failures`` maps a task id to the number of attempts that should fail
    before the underlying executor is allowed to run. Combined with
    ``persistent``, this drives the two recovery stories the assessment asks
    for: a transient failure that succeeds within its retry budget, and a
    persistent failure that exhausts the budget and stops safely.
    """

    def __init__(
        self,
        inner: TaskExecutor,
        failures: dict[str, int] | None = None,
        persistent: frozenset[str] = frozenset(),
    ) -> None:
        self._inner = inner
        self._remaining = dict(failures or {})
        self._persistent = persistent

    def execute(self, task: Task, inputs: dict[str, str]) -> TaskOutput:
        if task.id in self._persistent:
            raise TaskFailure(
                f"injected persistent failure in '{task.id}'", transient=False
            )
        remaining = self._remaining.get(task.id, 0)
        if remaining > 0:
            self._remaining[task.id] = remaining - 1
            raise TaskFailure(f"injected transient failure in '{task.id}'", transient=True)
        return self._inner.execute(task, inputs)

    def actor_for(self, task: Task) -> Actor | None:
        """Preserve optional acting-agent attribution through the wrapper."""
        resolver = getattr(self._inner, "actor_for", None)
        return resolver(task) if callable(resolver) else None
