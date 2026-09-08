"""Bounded recovery decisions, compensation execution, and event metrics."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict

from orchestrator.contracts import Event, EventType, RunMetrics, Task, TaskState
from orchestrator.executor import TaskOutput


class RecoveryAction(str, Enum):
    RETRY = "retry"
    FALLBACK = "fallback"
    COMPENSATE = "compensate"
    SAFE_STOP = "safe_stop"


class RecoveryDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: RecoveryAction
    reason: str


class CompensationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: str
    executed: bool
    succeeded: bool
    reason: str


class FallbackResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    handler: str
    executed: bool
    succeeded: bool
    reason: str
    output: TaskOutput | None = None


CompensationOutcome = bool | tuple[bool, str]
CompensationHandler = Callable[[Task, str], CompensationOutcome]
FallbackHandler = Callable[[Task, dict[str, str]], TaskOutput]


class RecoveryController:
    """Choose retry/compensation without owning workflow state."""

    def __init__(
        self,
        compensations: dict[str, CompensationHandler] | None = None,
        fallbacks: dict[str, FallbackHandler] | None = None,
    ) -> None:
        self._compensations = dict(compensations or {})
        self._fallbacks = dict(fallbacks or {})

    def register(self, name: str, handler: CompensationHandler) -> None:
        if not name.strip():
            raise ValueError("compensation name must not be empty")
        self._compensations[name] = handler

    def register_fallback(self, name: str, handler: FallbackHandler) -> None:
        """Register alternate task work under a stable recovery name.

        A task selects a named fallback through its existing recovery hook
        (``Task.compensation``). A task-id key is also accepted for a targeted
        fallback when no compensating action is declared. This keeps the frozen
        task contract unchanged while allowing fallback to precede compensation.
        """
        if not name.strip():
            raise ValueError("fallback name must not be empty")
        self._fallbacks[name] = handler

    def _fallback_name(self, task: Task) -> str | None:
        if task.id in self._fallbacks:
            return task.id
        if task.compensation is not None and task.compensation in self._fallbacks:
            return task.compensation
        return None

    def decide(self, task: Task, *, transient: bool) -> RecoveryDecision:
        if transient and task.retries_remaining > 0:
            return RecoveryDecision(
                action=RecoveryAction.RETRY,
                reason=(
                    f"transient failure; {task.retries_remaining} bounded "
                    "retry attempt(s) remain"
                ),
            )
        fallback_name = self._fallback_name(task)
        if fallback_name is not None:
            reason = (
                "non-transient failure skips retries"
                if not transient
                else "retry budget exhausted"
            )
            return RecoveryDecision(
                action=RecoveryAction.FALLBACK,
                reason=f"{reason}; execute fallback '{fallback_name}'",
            )
        if task.compensation:
            reason = (
                "non-transient failure skips retries"
                if not transient
                else "retry budget exhausted"
            )
            return RecoveryDecision(
                action=RecoveryAction.COMPENSATE,
                reason=f"{reason}; execute '{task.compensation}'",
            )
        return RecoveryDecision(
            action=RecoveryAction.SAFE_STOP,
            reason=(
                "non-transient failure skips retries and no compensation is configured"
                if not transient
                else "retry budget exhausted and no compensation is configured"
            ),
        )

    def fallback(
        self,
        task: Task,
        inputs: dict[str, str],
    ) -> FallbackResult:
        """Execute alternate work without making any governance decisions."""
        name = self._fallback_name(task)
        if name is None:
            return FallbackResult(
                handler="<none>",
                executed=False,
                succeeded=False,
                reason="no fallback handler is registered for the task",
            )
        handler = self._fallbacks[name]
        try:
            output = handler(task.model_copy(deep=True), dict(inputs))
            if not isinstance(output, TaskOutput):
                raise TypeError("fallback handler must return TaskOutput")
            if not output.succeeded:
                return FallbackResult(
                    handler=name,
                    executed=True,
                    succeeded=False,
                    reason=(
                        output.failure_reason
                        or output.summary
                        or "fallback handler reported failure"
                    ),
                    output=output,
                )
            return FallbackResult(
                handler=name,
                executed=True,
                succeeded=True,
                reason=output.summary or "fallback handler completed",
                output=output,
            )
        except Exception as exc:
            return FallbackResult(
                handler=name,
                executed=True,
                succeeded=False,
                reason=f"fallback handler failed closed: {type(exc).__name__}: {exc}",
            )

    def compensate(self, task: Task, failure_reason: str) -> CompensationResult:
        name = task.compensation
        if name is None:
            return CompensationResult(
                action="<none>",
                executed=False,
                succeeded=False,
                reason="no compensating action is configured",
            )
        handler = self._compensations.get(name)
        if handler is None:
            return CompensationResult(
                action=name,
                executed=False,
                succeeded=False,
                reason=f"compensating action '{name}' has no registered handler",
            )
        try:
            outcome = handler(task.model_copy(deep=True), failure_reason)
            if isinstance(outcome, tuple):
                succeeded, reason = outcome
            else:
                succeeded = outcome
                reason = "compensating action completed" if outcome else "compensating action failed"
            if not isinstance(succeeded, bool) or not isinstance(reason, str):
                raise TypeError("compensation must return bool or (bool, str)")
            return CompensationResult(
                action=name,
                executed=True,
                succeeded=succeeded,
                reason=reason,
            )
        except Exception as exc:
            return CompensationResult(
                action=name,
                executed=True,
                succeeded=False,
                reason=f"compensating action failed closed: {type(exc).__name__}: {exc}",
            )


def _payload_value(value: object) -> str | None:
    if isinstance(value, Enum):
        return str(value.value)
    return value if isinstance(value, str) else None


def _instant(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def compute_run_metrics(
    events: Iterable[Event],
    *,
    run_id: str | None = None,
) -> RunMetrics:
    """Project reliability metrics from immutable events and nothing else."""

    stream = tuple(events)
    run_ids = {event.run_id for event in stream}
    if run_id is None:
        if len(run_ids) > 1:
            raise ValueError("run_id is required when events contain multiple runs")
        selected = stream
    else:
        selected = tuple(event for event in stream if event.run_id == run_id)
    selected = tuple(sorted(selected, key=lambda event: event.seq))

    task_ids: set[str] = set()
    executed: set[str] = set()
    succeeded: set[str] = set()
    failed: set[str] = set()
    first_failures: dict[str, datetime] = {}
    recovery_durations: list[float] = []
    retry_count = 0
    rollback_count = 0
    approvals_requested = 0
    policy_denials = 0
    gate_denials = 0
    replans = 0
    started: datetime | None = None
    completed: datetime | None = None

    for event in selected:
        when = _instant(event.at)
        if event.type is EventType.RUN_STARTED and started is None:
            started = when
        elif event.type is EventType.RUN_COMPLETED:
            completed = when
        elif event.type is EventType.PLAN_CREATED:
            declared = event.payload.get("task_ids", ())
            if isinstance(declared, (list, tuple)):
                task_ids.update(item for item in declared if isinstance(item, str))

        if event.task_id is not None and event.type is EventType.TASK_STATE_CHANGED:
            task_ids.add(event.task_id)
            destination = _payload_value(event.payload.get("to"))
            if destination == TaskState.RUNNING.value:
                executed.add(event.task_id)
            if destination == TaskState.RETRYING.value:
                first_failures.setdefault(event.task_id, when)
            if destination == TaskState.FAILED.value:
                failed.add(event.task_id)
                first_failures.setdefault(event.task_id, when)
            if destination == TaskState.SUCCEEDED.value:
                succeeded.add(event.task_id)
            if destination in {TaskState.SUCCEEDED.value, TaskState.COMPENSATED.value}:
                began = first_failures.pop(event.task_id, None)
                if began is not None:
                    recovery_durations.append(max(0.0, (when - began).total_seconds()))

        if event.type is EventType.RETRY_ATTEMPTED:
            retry_count += 1
            if event.task_id is not None:
                first_failures.setdefault(event.task_id, when)
        elif event.type is EventType.COMPENSATION_EXECUTED:
            if event.payload.get("executed") is not False:
                rollback_count += 1
        elif event.type is EventType.APPROVAL_REQUESTED:
            approvals_requested += 1
        elif event.type is EventType.POLICY_EVALUATED:
            if _payload_value(event.payload.get("effect")) == "deny":
                policy_denials += 1
        elif event.type is EventType.GATE_EVALUATED:
            if event.payload.get("passed") is False:
                gate_denials += 1
        elif event.type is EventType.REPLAN_TRIGGERED:
            replans += 1

    tasks_total = len(task_ids)
    executed_total = len(executed)
    success_rate = len(succeeded) / tasks_total if tasks_total else 0.0
    retry_rate = retry_count / executed_total if executed_total else 0.0
    rollback_rate = rollback_count / executed_total if executed_total else 0.0
    end_to_end = (
        max(0.0, (completed - started).total_seconds())
        if started is not None and completed is not None
        else 0.0
    )
    mttr = (
        sum(recovery_durations) / len(recovery_durations)
        if recovery_durations
        else None
    )
    return RunMetrics(
        tasks_total=tasks_total,
        tasks_succeeded=len(succeeded),
        tasks_failed=len(failed),
        success_rate=success_rate,
        retry_count=retry_count,
        retry_rate=retry_rate,
        rollback_count=rollback_count,
        rollback_rate=rollback_rate,
        mttr_seconds=mttr,
        end_to_end_seconds=end_to_end,
        approvals_requested=approvals_requested,
        policy_denials=policy_denials,
        gate_denials=gate_denials,
        replans=replans,
    )


RecoveryManager = RecoveryController
metrics_from_events = compute_run_metrics
