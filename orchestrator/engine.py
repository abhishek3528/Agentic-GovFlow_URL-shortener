"""Governed execution engine for versioned task DAGs.

The engine is the sole owner of workflow state, gate decisions, event writes,
and artifact provenance.  Executors only perform task work and return content.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType
from typing import TypeAlias

from orchestrator.clock import Clock, FixedClock, IdGen, SeededIdGen
from orchestrator.approval import ApprovalController, ApprovalError
from orchestrator.contracts import (
    Actor,
    ActorKind,
    Approval,
    Artifact,
    ContextVersion,
    Decision,
    Event,
    EventType,
    Gate,
    GateKind,
    GateResult,
    Plan,
    PolicyDecision,
    PolicyEffect,
    RunMetrics,
    RunState,
    SYSTEM_ACTOR,
    TERMINAL_RUN_STATES,
    TERMINAL_TASK_STATES,
    Task,
    TaskState,
    is_legal_run_transition,
    is_legal_task_transition,
)
from orchestrator.events import EventStore
from orchestrator.executor import TaskExecutor, TaskFailure, TaskOutput
from orchestrator.graph import DependencyGraph, GraphValidationError
from orchestrator.recovery import (
    RecoveryAction,
    RecoveryController,
    compute_run_metrics,
)


class EngineError(RuntimeError):
    """Base class for governed execution failures."""


class IllegalTransitionError(EngineError):
    """Raised when a requested state change is outside the frozen contract."""


class ReplanError(EngineError):
    """Raised when selective invalidation cannot be performed safely."""


GateOutcome: TypeAlias = bool | tuple[bool, str]
GateEvaluator: TypeAlias = Callable[
    [Gate, Task, Mapping[str, Artifact]], GateOutcome
]


class OrchestrationEngine:
    """Execute one governed run against a validated :class:`Plan`.

    The scheduler is deterministic: all nodes that become ready in one pass are
    promoted together, then executed in stable topological order.  Independent
    fork branches are therefore explicit without introducing nondeterministic
    event ordering into evidence.
    """

    def __init__(
        self,
        plan: Plan,
        executor: TaskExecutor,
        *,
        event_store: EventStore | None = None,
        clock: Clock | None = None,
        id_gen: IdGen | None = None,
        run_id: str | None = None,
        gate_evaluator: GateEvaluator | None = None,
        approval_controller: ApprovalController | None = None,
        recovery_controller: RecoveryController | None = None,
        required_policies: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        self._clock = clock or FixedClock()
        self._id_gen = id_gen or SeededIdGen()
        self.run_id = run_id or self._id_gen.next_id("run")
        self._executor = executor
        self._events = event_store or EventStore()
        self._gate_evaluator = gate_evaluator
        self._approval_controller = approval_controller or ApprovalController()
        self._recovery_controller = recovery_controller or RecoveryController()

        self._plan = plan.model_copy(deep=True)
        self._graph = DependencyGraph(self._plan)
        non_pending = [
            task.id
            for task in self._graph.topological_order()
            if task.state is not TaskState.PENDING
        ]
        if non_pending:
            raise GraphValidationError(
                "a new run requires pending tasks; non-pending task(s): "
                + ", ".join(non_pending)
            )
        self._plan_history: list[Plan] = [self._plan.model_copy(deep=True)]
        self._contexts: list[ContextVersion] = []
        self._decisions: list[Decision] = []
        self._artifacts: list[Artifact] = []
        self._gate_results: dict[tuple[str, GateKind, str], GateResult] = {}
        self._policy_decisions: list[tuple[int, PolicyDecision]] = []
        self._required_policies = {
            task_id: frozenset(policy_ids)
            for task_id, policy_ids in (required_policies or {}).items()
        }
        unknown_policy_tasks = sorted(set(self._required_policies) - set(self._graph.task_ids))
        if unknown_policy_tasks:
            raise EngineError(
                "required policies reference unknown task(s): "
                + ", ".join(unknown_policy_tasks)
            )
        self._run_state = RunState.PENDING
        self._started_at: str | None = None
        self._completed_at: str | None = None

    @property
    def run_state(self) -> RunState:
        return self._run_state

    @property
    def current_plan(self) -> Plan:
        return self._plan.model_copy(deep=True)

    @property
    def plans(self) -> tuple[Plan, ...]:
        # Prior revisions are immutable snapshots.  The active revision is
        # projected from the live graph so terminal task states are persisted
        # in result/evidence bundles instead of looking permanently pending.
        prior = self._plan_history[:-1]
        return tuple(
            plan.model_copy(deep=True) for plan in (*prior, self._plan)
        )

    @property
    def artifacts(self) -> tuple[Artifact, ...]:
        return tuple(artifact.model_copy(deep=True) for artifact in self._artifacts)

    @property
    def gate_results(self) -> tuple[GateResult, ...]:
        return tuple(self._gate_results.values())

    @property
    def policy_decisions(self) -> tuple[PolicyDecision, ...]:
        return tuple(decision for _, decision in self._policy_decisions)

    @property
    def context_versions(self) -> tuple[ContextVersion, ...]:
        return tuple(self._contexts)

    @property
    def decisions(self) -> tuple[Decision, ...]:
        return tuple(self._decisions)

    @property
    def approvals(self) -> tuple[Approval, ...]:
        return self._approval_controller.decisions

    @property
    def metrics(self) -> RunMetrics:
        """Current metrics projected exclusively from this run's events."""
        return compute_run_metrics(self.events, run_id=self.run_id)

    @property
    def events(self) -> tuple[Event, ...]:
        return self._events.events(self.run_id)

    @property
    def started_at(self) -> str | None:
        return self._started_at

    @property
    def completed_at(self) -> str | None:
        return self._completed_at

    def task(self, task_id: str) -> Task:
        """Return a defensive snapshot of one current-plan task."""
        return self._graph.task(task_id).model_copy(deep=True)

    def ready_task_ids(self) -> tuple[str, ...]:
        """Expose the next topological frontier without mutating the run."""
        return tuple(task.id for task in self._graph.ready_tasks())

    def promote_ready_tasks(self, *, actor: Actor = SYSTEM_ACTOR) -> tuple[str, ...]:
        """Move the complete dependency-satisfied frontier to ``READY``.

        This is the only public way to request a readiness transition.  Direct
        low-level state changes are rejected because a sequence of individually
        legal moves could otherwise bypass graph and gate governance.
        """
        if self._run_state is not RunState.RUNNING:
            raise EngineError("tasks can become ready only while the run is running")
        frontier = self._graph.ready_tasks()
        for task in frontier:
            self._transition_task(
                task.id,
                TaskState.READY,
                actor=self._acting_actor(task, actor),
                reason="all dependencies succeeded",
            )
        return tuple(task.id for task in frontier)

    def start(self, *, actor: Actor = SYSTEM_ACTOR) -> None:
        if self._run_state is not RunState.PENDING:
            raise EngineError(f"run '{self.run_id}' has already started")
        started = self._emit(
            EventType.RUN_STARTED,
            actor=actor,
            summary=f"run '{self.run_id}' started",
            payload={"plan_revision": self._plan.revision},
        )
        self._started_at = started.at
        self._emit(
            EventType.PLAN_CREATED,
            actor=actor,
            summary=f"plan revision {self._plan.revision} activated",
            payload={
                "revision": self._plan.revision,
                "context_version": self._plan.context_version,
                "task_ids": list(self._graph.task_ids),
            },
        )
        self._transition_run(RunState.RUNNING, actor=actor, reason="run started")

    def record_context_version(
        self,
        context: ContextVersion,
        *,
        actor: Actor = SYSTEM_ACTOR,
    ) -> ContextVersion:
        """Record immutable requirement context without changing the plan.

        The initial context is scenario input, while later contexts normally
        arrive through :meth:`replan`.  Both use the same append-only event
        vocabulary so evidence consumers do not need a side channel.
        """
        if context.version != self._plan.context_version:
            raise EngineError(
                "recorded context version must match the active plan context"
            )
        if any(existing.version == context.version for existing in self._contexts):
            raise EngineError(f"context version {context.version} is already recorded")
        self._contexts.append(context)
        self._emit(
            EventType.CONTEXT_VERSION_CREATED,
            actor=actor,
            summary=f"context version {context.version} created",
            payload=context.model_dump(mode="json"),
        )
        return context

    def record_decision(
        self,
        decision: Decision,
        *,
        actor: Actor | None = None,
    ) -> Decision:
        """Persist an attributable engineering decision and its lineage."""
        if decision.context_version is not None and not any(
            context.version == decision.context_version for context in self._contexts
        ):
            raise EngineError(
                f"decision '{decision.id}' references an unrecorded context version"
            )
        if decision.task_id is not None:
            self._graph.task(decision.task_id)
        if any(existing.id == decision.id for existing in self._decisions):
            raise EngineError(f"decision '{decision.id}' is already recorded")
        self._decisions.append(decision)
        event_actor = actor or decision.actor
        self._emit(
            EventType.DECISION_RECORDED,
            actor=event_actor,
            task_id=decision.task_id,
            summary=decision.summary,
            payload={
                **decision.model_dump(mode="json"),
                "plan_revision": self._plan.revision,
            },
        )
        return decision

    def run(self, *, actor: Actor = SYSTEM_ACTOR) -> RunState:
        """Run all reachable work, returning the resulting run state."""
        if self._run_state is RunState.PENDING:
            self.start(actor=actor)
        if self._run_state is not RunState.RUNNING:
            return self._run_state

        while self._run_state is RunState.RUNNING:
            progress = False

            # Descendants of denied or failed work can never become ready in
            # this plan revision; make that explicit instead of deadlocking.
            for task in self._graph.blocked_by_terminal_dependency():
                self._transition_task(
                    task.id,
                    TaskState.SKIPPED,
                    actor=self._acting_actor(task, actor),
                    reason="a dependency did not succeed",
                )
                progress = True

            frontier = self.promote_ready_tasks(actor=actor)
            if frontier:
                progress = True

            # Promote the complete frontier first.  This is the observable fork
            # boundary; no branch can accidentally unlock a join early.
            ready_ids = tuple(
                task.id
                for task in self._graph.topological_order()
                if task.state is TaskState.READY
            )
            for task_id in ready_ids:
                if self._run_state is not RunState.RUNNING:
                    break
                self.execute_task(task_id, actor=actor)
                progress = True

            if self._run_state is not RunState.RUNNING:
                break

            states = tuple(task.state for task in self._graph.topological_order())
            if states and all(state is TaskState.SUCCEEDED for state in states):
                self._finish(RunState.SUCCEEDED, "all tasks succeeded", actor)
                break
            if not states:
                self._finish(RunState.SUCCEEDED, "empty plan completed", actor)
                break
            if any(state is TaskState.AWAITING_APPROVAL for state in states):
                self._transition_run(
                    RunState.AWAITING_APPROVAL,
                    actor=actor,
                    reason="human approval required",
                )
                break
            if all(state in TERMINAL_TASK_STATES for state in states):
                self._finish(
                    RunState.FAILED,
                    "one or more tasks did not succeed",
                    actor,
                )
                break
            if not progress:
                self._finish(
                    RunState.FAILED,
                    "no task can make progress under the current plan",
                    actor,
                )
                break

        return self._run_state

    def record_policy_decision(
        self,
        decision: PolicyDecision,
        *,
        actor: Actor = SYSTEM_ACTOR,
    ) -> PolicyDecision:
        """Record a policy verdict for the active plan revision."""
        self._graph.task(decision.task_id)
        self._policy_decisions.append((self._plan.revision, decision))
        self._emit(
            EventType.POLICY_EVALUATED,
            actor=actor,
            task_id=decision.task_id,
            summary=(
                f"policy '{decision.policy_id}' "
                f"{'allowed' if decision.effect is PolicyEffect.ALLOW else 'denied'}"
            ),
            payload={
                **decision.model_dump(mode="json"),
                "plan_revision": self._plan.revision,
            },
        )
        return decision

    def decide_approval(self, approval: Approval, *, resume: bool = True) -> RunState:
        """Apply one correlated human approval and optionally resume scheduling.

        ``resume=False`` completes only the approved task and leaves the run in
        ``RUNNING``.  This gives a scenario a deterministic boundary at which
        newly supplied human context can trigger a governed re-plan before any
        downstream work is scheduled.  The default preserves the normal
        approve-and-continue behavior.
        """
        if self._run_state is not RunState.AWAITING_APPROVAL:
            raise EngineError("the run is not awaiting approval")
        if approval.actor.kind is not ActorKind.HUMAN:
            raise EngineError("approval decisions must be made by a human actor")
        task = self._graph.task(approval.task_id)
        if task.state is not TaskState.AWAITING_APPROVAL:
            raise EngineError(f"task '{task.id}' is not awaiting approval")
        try:
            self._approval_controller.decide(approval)
        except ApprovalError as exc:
            raise EngineError(str(exc)) from exc

        self._emit(
            EventType.APPROVAL_DECIDED,
            actor=approval.actor,
            task_id=task.id,
            summary=f"human approval {'granted' if approval.granted else 'denied'}",
            payload={
                **approval.model_dump(mode="json"),
                "plan_revision": self._plan.revision,
            },
        )
        if not approval.granted:
            self._transition_task(
                task.id,
                TaskState.BLOCKED,
                actor=approval.actor,
                reason=approval.rationale or "human approval denied",
            )
            self._transition_run(
                RunState.RUNNING,
                actor=approval.actor,
                reason="human approval decision received",
            )
            return self.run(actor=approval.actor)

        acting_actor = self._acting_actor(task, approval.actor)
        input_contents, input_artifacts, input_error = self._resolve_inputs(task)
        if input_error is not None:
            self._emit_input_denial(task, input_error, acting_actor)
            self._transition_task(
                task.id,
                TaskState.BLOCKED,
                actor=acting_actor,
                reason=input_error,
            )
            self._transition_run(
                RunState.RUNNING,
                actor=approval.actor,
                reason="approval received but task inputs failed closed",
            )
            return self.run(actor=approval.actor)

        self._transition_run(
            RunState.RUNNING,
            actor=approval.actor,
            reason="human approval granted",
        )
        self._transition_task(
            task.id,
            TaskState.RUNNING,
            actor=acting_actor,
            reason="human approval granted",
        )
        task.attempts += 1
        self._execute_running_task(task, input_contents, input_artifacts, acting_actor)
        if self._run_state is RunState.RUNNING and resume:
            return self.run(actor=approval.actor)
        return self._run_state

    def execute_task(self, task_id: str, *, actor: Actor = SYSTEM_ACTOR) -> TaskState:
        """Apply entry controls, execute one READY task, and apply exit controls."""
        task = self._graph.task(task_id)
        if self._run_state is not RunState.RUNNING:
            raise EngineError("tasks can execute only while the run is running")
        if task.state is not TaskState.READY:
            raise EngineError(
                f"task '{task_id}' must be ready before execution; it is '{task.state.value}'"
            )

        actor = self._acting_actor(task, actor)

        if not self._policies_allow(task, actor):
            self._transition_task(
                task.id,
                TaskState.BLOCKED,
                actor=actor,
                reason="one or more policies denied execution",
            )
            return task.state

        input_contents, input_artifacts, input_error = self._resolve_inputs(task)
        if input_error is not None:
            self._emit_input_denial(task, input_error, actor)
            self._transition_task(task.id, TaskState.BLOCKED, actor=actor, reason=input_error)
            return task.state

        if not self._evaluate_gates(task, task.entry_gates, input_artifacts, actor):
            self._transition_task(
                task.id,
                TaskState.BLOCKED,
                actor=actor,
                reason="one or more entry gates denied execution",
            )
            return task.state

        if task.requires_approval:
            self._approval_controller.request(task.id)
            self._transition_task(
                task.id,
                TaskState.AWAITING_APPROVAL,
                actor=actor,
                reason="high-impact work requires human approval",
            )
            self._emit(
                EventType.APPROVAL_REQUESTED,
                actor=actor,
                task_id=task.id,
                summary="human approval requested",
                payload={
                    "impact": task.impact.value,
                    "plan_revision": self._plan.revision,
                },
            )
            return task.state

        task.attempts += 1
        self._transition_task(task.id, TaskState.RUNNING, actor=actor, reason="entry controls passed")
        self._execute_running_task(task, input_contents, input_artifacts, actor)
        return task.state

    def _policies_allow(self, task: Task, actor: Actor) -> bool:
        latest = {
            decision.policy_id: decision
            for revision, decision in self._policy_decisions
            if revision == self._plan.revision and decision.task_id == task.id
        }
        for policy_id in sorted(self._required_policies.get(task.id, frozenset())):
            if policy_id in latest:
                continue
            missing = PolicyDecision(
                policy_id=policy_id,
                task_id=task.id,
                effect=PolicyEffect.DENY,
                reason="required policy was not evaluated; failed closed",
                evaluated_at=self._clock.iso(),
            )
            self.record_policy_decision(missing, actor=actor)
            latest[policy_id] = missing
        return all(not decision.denied for decision in latest.values())

    def _execute_running_task(
        self,
        task: Task,
        input_contents: dict[str, str],
        input_artifacts: Mapping[str, Artifact],
        actor: Actor,
    ) -> None:
        """Execute attempts until success or a governed terminal disposition."""
        while self._run_state is RunState.RUNNING and task.state is TaskState.RUNNING:
            try:
                output = self._executor.execute(task.model_copy(deep=True), input_contents)
            except TaskFailure as exc:
                if self._recover_failure(
                    task,
                    exc.reason,
                    exc.transient,
                    input_contents,
                    input_artifacts,
                    actor,
                ):
                    continue
                return
            except Exception as exc:  # executor failures must not escape governance
                reason = f"executor raised {type(exc).__name__}: {exc}"
                self._recover_failure(
                    task,
                    reason,
                    False,
                    input_contents,
                    input_artifacts,
                    actor,
                )
                return

            if not output.succeeded:
                reason = output.failure_reason or output.summary or "executor reported failure"
                self._recover_failure(
                    task,
                    reason,
                    False,
                    input_contents,
                    input_artifacts,
                    actor,
                )
                return

            self._complete_successful_output(
                task, output, input_artifacts, actor
            )
            return

    def _complete_successful_output(
        self,
        task: Task,
        output: TaskOutput,
        input_artifacts: Mapping[str, Artifact],
        actor: Actor,
    ) -> None:
        """Apply the one governed completion path to primary or fallback work."""
        contract_error = self._validate_output_contract(task, output)
        if contract_error is not None:
            self._emit_input_denial(task, contract_error, actor, kind=GateKind.EXIT)
            self._transition_task(task.id, TaskState.BLOCKED, actor=actor, reason=contract_error)
            return

        produced = self._record_artifacts(task, output, input_artifacts, actor)
        all_valid = self._record_validations(task, output, actor)
        gate_inputs = {**input_artifacts, **produced}
        exit_passed = self._evaluate_gates(task, task.exit_gates, gate_inputs, actor)
        if not all_valid or not exit_passed:
            reason = (
                "one or more validation results failed"
                if not all_valid
                else "one or more exit gates denied completion"
            )
            self._transition_task(task.id, TaskState.BLOCKED, actor=actor, reason=reason)
            return

        self._transition_task(
            task.id,
            TaskState.SUCCEEDED,
            actor=actor,
            reason=output.summary or "task completed",
        )

    def _recover_failure(
        self,
        task: Task,
        failure_reason: str,
        transient: bool,
        input_contents: dict[str, str],
        input_artifacts: Mapping[str, Artifact],
        actor: Actor,
    ) -> bool:
        decision = self._recovery_controller.decide(task, transient=transient)
        if decision.action is RecoveryAction.RETRY:
            self._transition_task(
                task.id,
                TaskState.RETRYING,
                actor=actor,
                reason=failure_reason,
            )
            self._emit(
                EventType.RETRY_ATTEMPTED,
                actor=actor,
                task_id=task.id,
                summary=f"bounded retry scheduled for task '{task.id}'",
                payload={
                    "failed_attempt": task.attempts,
                    "next_attempt": task.attempts + 1,
                    "retry_budget": task.retry_budget,
                    "failure_reason": failure_reason,
                    "recovery_reason": decision.reason,
                },
            )
            task.attempts += 1
            self._transition_task(
                task.id,
                TaskState.RUNNING,
                actor=actor,
                reason="bounded retry started",
            )
            return True

        if decision.action is RecoveryAction.FALLBACK:
            fallback = self._recovery_controller.fallback(task, input_contents)
            # EventType has no fallback-specific member and contracts are frozen,
            # so the discriminator on DECISION_RECORDED preserves audit clarity.
            self._emit(
                EventType.DECISION_RECORDED,
                actor=actor,
                task_id=task.id,
                summary=(
                    f"fallback handler '{fallback.handler}' "
                    f"{'succeeded' if fallback.succeeded else 'failed'}"
                ),
                payload={
                    "recovery_action": "fallback",
                    "handler": fallback.handler,
                    "executed": fallback.executed,
                    "succeeded": fallback.succeeded,
                    "reason": fallback.reason,
                    "failure_reason": failure_reason,
                },
            )
            if fallback.succeeded and fallback.output is not None:
                self._complete_successful_output(
                    task, fallback.output, input_artifacts, actor
                )
                return False

        self._transition_task(
            task.id,
            TaskState.FAILED,
            actor=actor,
            reason=failure_reason,
        )
        if decision.action in {RecoveryAction.FALLBACK, RecoveryAction.COMPENSATE} and task.compensation:
            compensation = self._recovery_controller.compensate(task, failure_reason)
            self._emit(
                EventType.COMPENSATION_EXECUTED,
                actor=actor,
                task_id=task.id,
                summary=(
                    f"compensating action '{compensation.action}' "
                    f"{'succeeded' if compensation.succeeded else 'failed'}"
                ),
                payload={
                    **compensation.model_dump(mode="json"),
                    "failure_reason": failure_reason,
                },
            )
            if compensation.succeeded:
                self._transition_task(
                    task.id,
                    TaskState.COMPENSATED,
                    actor=actor,
                    reason=compensation.reason,
                )

        terminal_reason = f"recovery exhausted for task '{task.id}': {decision.reason}"
        self._emit(
            EventType.SAFE_STOP_TRIGGERED,
            actor=actor,
            task_id=task.id,
            summary=terminal_reason,
            payload={
                "failure_reason": failure_reason,
                "attempts": task.attempts,
                "retry_budget": task.retry_budget,
                "compensation": task.compensation,
            },
        )
        self._finish(RunState.SAFE_STOPPED, terminal_reason, actor)
        return False

    def transition_task(
        self,
        task_id: str,
        destination: TaskState,
        *,
        actor: Actor = SYSTEM_ACTOR,
        reason: str = "",
    ) -> None:
        """Reject direct state mutation, including otherwise legal bypasses."""
        task = self._graph.task(task_id)
        source = task.state
        if not is_legal_task_transition(source, destination):
            raise IllegalTransitionError(
                f"illegal task transition for '{task_id}': "
                f"{source.value} -> {destination.value}"
            )
        raise EngineError(
            "direct task transitions are not permitted; use run, "
            "promote_ready_tasks, execute_task, or replan"
        )

    def _transition_task(
        self,
        task_id: str,
        destination: TaskState,
        *,
        actor: Actor = SYSTEM_ACTOR,
        reason: str = "",
    ) -> None:
        task = self._graph.task(task_id)
        source = task.state
        if not is_legal_task_transition(source, destination):
            raise IllegalTransitionError(
                f"illegal task transition for '{task_id}': "
                f"{source.value} -> {destination.value}"
            )
        task.state = destination
        self._emit(
            EventType.TASK_STATE_CHANGED,
            actor=actor,
            task_id=task.id,
            summary=f"task state changed: {source.value} -> {destination.value}",
            payload={
                "from": source.value,
                "to": destination.value,
                "reason": reason,
                "plan_revision": self._plan.revision,
            },
        )

    def transition_run(
        self,
        destination: RunState,
        *,
        actor: Actor = SYSTEM_ACTOR,
        reason: str = "",
    ) -> None:
        """Reject direct run mutation, including premature success claims."""
        source = self._run_state
        if not is_legal_run_transition(source, destination):
            raise IllegalTransitionError(
                f"illegal run transition for '{self.run_id}': "
                f"{source.value} -> {destination.value}"
            )
        raise EngineError(
            "direct run transitions are not permitted; use start, run, or replan"
        )

    def _transition_run(
        self,
        destination: RunState,
        *,
        actor: Actor = SYSTEM_ACTOR,
        reason: str = "",
    ) -> None:
        source = self._run_state
        if not is_legal_run_transition(source, destination):
            raise IllegalTransitionError(
                f"illegal run transition for '{self.run_id}': "
                f"{source.value} -> {destination.value}"
            )
        self._run_state = destination
        self._emit(
            EventType.RUN_STATE_CHANGED,
            actor=actor,
            summary=f"run state changed: {source.value} -> {destination.value}",
            payload={"from": source.value, "to": destination.value, "reason": reason},
        )

    def replan(
        self,
        new_context: ContextVersion,
        *,
        changed_task_ids: Iterable[str] = (),
        changed_artifact_ids: Iterable[str] = (),
        replacement_tasks: Iterable[Task] | None = None,
        actor: Actor = SYSTEM_ACTOR,
    ) -> Plan:
        """Selectively invalidate descendants and activate a new plan revision.

        ``ContextVersion`` deliberately contains no task mapping, so callers
        must identify at least one changed task or artifact as the causal seed.
        Descendant discovery itself is owned by the graph and artifact lineage.
        """
        if self._run_state is not RunState.RUNNING:
            raise ReplanError("re-planning requires a running workflow")
        if new_context.version <= self._plan.context_version:
            raise ReplanError("new context version must increase")
        if new_context.supersedes != self._plan.context_version:
            raise ReplanError(
                "new context must supersede the active plan's context version"
            )

        task_seeds = set(changed_task_ids)
        artifact_seeds = set(changed_artifact_ids)
        known_artifacts = {artifact.id: artifact for artifact in self._artifacts}
        unknown_artifacts = sorted(artifact_seeds - known_artifacts.keys())
        if unknown_artifacts:
            raise ReplanError(
                "unknown changed artifact(s): " + ", ".join(unknown_artifacts)
            )
        task_seeds.update(
            known_artifacts[artifact_id].produced_by_task
            for artifact_id in artifact_seeds
        )
        if not task_seeds:
            raise ReplanError("at least one changed task or artifact is required")
        try:
            affected_tasks = set(self._graph.descendants(task_seeds))
        except KeyError as exc:
            raise ReplanError(str(exc)) from exc

        replacement_source = (
            tuple(task.model_copy(deep=True) for task in replacement_tasks)
            if replacement_tasks is not None
            else None
        )
        # Validate replacement structure before changing run, task, or artifact
        # state.  A malformed revised graph must leave the active revision
        # untouched rather than stranding the run halfway through a re-plan.
        if replacement_source is not None:
            candidate = Plan(
                revision=self._plan.revision + 1,
                context_version=new_context.version,
                tasks=replacement_source,
                created_at=self._plan.created_at,
                supersedes=self._plan.revision,
                change_reason=new_context.change_reason,
            )
            try:
                DependencyGraph(candidate)
            except GraphValidationError as exc:
                raise ReplanError(f"revised plan is invalid: {exc}") from exc

        self._emit(
            EventType.REPLAN_TRIGGERED,
            actor=actor,
            summary=f"re-plan triggered for context version {new_context.version}",
            payload={
                "prior_context_version": self._plan.context_version,
                "new_context_version": new_context.version,
                "changed_task_ids": sorted(task_seeds),
                "changed_artifact_ids": sorted(artifact_seeds),
            },
        )
        self._transition_run(RunState.REPLANNING, actor=actor, reason=new_context.change_reason or "context changed")
        self._contexts.append(new_context)
        self._emit(
            EventType.CONTEXT_VERSION_CREATED,
            actor=actor,
            summary=f"context version {new_context.version} created",
            payload=new_context.model_dump(mode="json"),
        )

        invalidated_artifact_ids = self._invalidate_artifacts(affected_tasks, artifact_seeds, actor)
        for task in self._graph.topological_order():
            if task.id not in affected_tasks or task.state is TaskState.STALE:
                continue
            self._approval_controller.cancel_request(task.id)
            self._transition_task(
                task.id,
                TaskState.STALE,
                actor=actor,
                reason=f"invalidated by context version {new_context.version}",
            )
            self._emit(
                EventType.TASK_INVALIDATED,
                actor=actor,
                task_id=task.id,
                summary="task invalidated by upstream change",
                payload={"new_context_version": new_context.version},
            )

        prior_revision = self._plan.revision
        # Preserve the completed live state of the superseded revision.  The
        # initial history entry was captured when every task was pending; a
        # re-plan must retain what actually ran in v1, not only its original
        # declaration.
        self._plan_history[-1] = self._plan.model_copy(deep=True)
        tasks = self._build_replacement_tasks(replacement_source, affected_tasks)
        revised = Plan(
            revision=prior_revision + 1,
            context_version=new_context.version,
            tasks=tasks,
            created_at=self._clock.iso(),
            supersedes=prior_revision,
            change_reason=new_context.change_reason,
        )
        try:
            revised_graph = DependencyGraph(revised)
        except GraphValidationError as exc:
            raise ReplanError(f"revised plan is invalid: {exc}") from exc

        self._plan = revised
        self._graph = revised_graph
        self._emit(
            EventType.PLAN_REVISED,
            actor=actor,
            summary=f"plan revision {revised.revision} supersedes {prior_revision}",
            payload={
                "revision": revised.revision,
                "supersedes": prior_revision,
                "context_version": new_context.version,
                "affected_task_ids": sorted(affected_tasks),
                "invalidated_artifact_ids": sorted(invalidated_artifact_ids),
            },
        )

        for task_id in sorted(affected_tasks):
            if task_id in self._graph.task_ids and self._graph.task(task_id).state is TaskState.STALE:
                self._transition_task(
                    task_id,
                    TaskState.PENDING,
                    actor=actor,
                    reason=f"scheduled in plan revision {revised.revision}",
                )

        # Capture the declarative revision after affected nodes have been reset.
        self._plan_history.append(self._plan.model_copy(deep=True))
        self._transition_run(RunState.RUNNING, actor=actor, reason="revised plan activated")
        return self.current_plan

    def _resolve_inputs(
        self, task: Task
    ) -> tuple[dict[str, str], dict[str, Artifact], str | None]:
        contents: dict[str, str] = {}
        selected: dict[str, Artifact] = {}
        for name in task.consumes:
            producer = self._graph.producer_for(name)
            if producer is None:
                return {}, {}, f"required artifact '{name}' has no declared producer"
            candidates = [
                artifact
                for artifact in self._artifacts
                if artifact.name == name and artifact.produced_by_task == producer.id
            ]
            if not candidates:
                return {}, {}, f"required artifact '{name}' is missing"
            artifact = max(candidates, key=lambda item: item.version)
            if artifact.stale:
                return {}, {}, f"required artifact '{name}' is stale"
            if producer.state is not TaskState.SUCCEEDED:
                return {}, {}, f"producer '{producer.id}' has not succeeded"
            if not self._exit_gates_passed(producer):
                return {}, {}, f"producer '{producer.id}' has not passed all exit gates"
            selected[name] = artifact
            # Executors consume content rather than provenance metadata.  This
            # in-memory registry intentionally keeps content out of Artifact.
            contents[name] = self._artifact_content[artifact.id]
        return contents, selected, None

    @property
    def _artifact_content(self) -> dict[str, str]:
        # Lazily created to keep the public Artifact contract untouched.
        content = getattr(self, "__artifact_content", None)
        if content is None:
            content = {}
            setattr(self, "__artifact_content", content)
        return content

    def _evaluate_gates(
        self,
        task: Task,
        gates: Iterable[Gate],
        artifacts: Mapping[str, Artifact],
        actor: Actor,
    ) -> bool:
        all_passed = True
        for gate in gates:
            if self._gate_evaluator is None:
                passed, reason = False, "no evaluator configured; gate failed closed"
            else:
                try:
                    safe_artifacts = MappingProxyType(
                        {
                            name: artifact.model_copy(deep=True)
                            for name, artifact in artifacts.items()
                        }
                    )
                    outcome = self._gate_evaluator(
                        gate, task.model_copy(deep=True), safe_artifacts
                    )
                    if isinstance(outcome, tuple):
                        passed, reason = outcome
                    else:
                        passed, reason = outcome, "gate evaluator returned a boolean verdict"
                    if not isinstance(passed, bool) or not isinstance(reason, str):
                        raise TypeError("gate evaluator must return bool or (bool, str)")
                except Exception as exc:
                    passed = False
                    reason = f"gate evaluator failed closed: {type(exc).__name__}: {exc}"

            result = GateResult(
                gate_id=gate.id,
                task_id=task.id,
                kind=gate.kind,
                passed=passed,
                reason=reason,
                evaluated_at=self._clock.iso(),
            )
            self._gate_results[(task.id, gate.kind, gate.id)] = result
            self._emit(
                EventType.GATE_EVALUATED,
                actor=actor,
                task_id=task.id,
                summary=f"gate '{gate.id}' {'passed' if passed else 'denied'}",
                payload=result.model_dump(mode="json"),
            )
            all_passed = all_passed and passed
        return all_passed

    def _exit_gates_passed(self, task: Task) -> bool:
        return all(
            (
                result := self._gate_results.get((task.id, GateKind.EXIT, gate.id))
            )
            is not None
            and result.passed
            for gate in task.exit_gates
        )

    def _validate_output_contract(self, task: Task, output: TaskOutput) -> str | None:
        declared = set(task.produces)
        actual = set(output.artifacts)
        missing = sorted(declared - actual)
        extra = sorted(actual - declared)
        if missing:
            return "executor omitted declared artifact(s): " + ", ".join(missing)
        if extra:
            return "executor returned undeclared artifact(s): " + ", ".join(extra)
        return None

    def _record_artifacts(
        self,
        task: Task,
        output: TaskOutput,
        inputs: Mapping[str, Artifact],
        actor: Actor,
    ) -> dict[str, Artifact]:
        produced: dict[str, Artifact] = {}
        lineage = tuple(artifact.id for artifact in inputs.values())
        for name in task.produces:
            content = output.artifacts[name]
            version = 1 + max(
                (artifact.version for artifact in self._artifacts if artifact.name == name),
                default=0,
            )
            artifact = Artifact(
                id=self._id_gen.next_id("artifact"),
                name=name,
                version=version,
                produced_by_task=task.id,
                context_version=self._plan.context_version,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                derived_from=lineage,
                created_at=self._clock.iso(),
            )
            self._artifacts.append(artifact)
            self._artifact_content[artifact.id] = content
            produced[name] = artifact
            self._emit(
                EventType.ARTIFACT_PRODUCED,
                actor=actor,
                task_id=task.id,
                summary=f"artifact '{name}' version {version} produced",
                payload=artifact.model_dump(mode="json"),
            )
        return produced

    def _record_validations(self, task: Task, output: TaskOutput, actor: Actor) -> bool:
        all_passed = True
        for name, passed in output.validation_results.items():
            self._emit(
                EventType.VALIDATION_EXECUTED,
                actor=actor,
                task_id=task.id,
                summary=f"validation '{name}' {'passed' if passed else 'failed'}",
                payload={"name": name, "passed": passed},
            )
            all_passed = all_passed and passed
        return all_passed

    def _emit_input_denial(
        self,
        task: Task,
        reason: str,
        actor: Actor,
        *,
        kind: GateKind = GateKind.ENTRY,
    ) -> None:
        self._emit(
            EventType.GATE_EVALUATED,
            actor=actor,
            task_id=task.id,
            summary="artifact contract denied progress",
            payload={
                "gate_id": "artifact-input-contract" if kind is GateKind.ENTRY else "artifact-output-contract",
                "task_id": task.id,
                "kind": kind.value,
                "passed": False,
                "reason": reason,
            },
        )

    def _invalidate_artifacts(
        self,
        affected_tasks: set[str],
        artifact_seeds: set[str],
        actor: Actor,
    ) -> set[str]:
        invalidated = set(artifact_seeds)
        invalidated.update(
            artifact.id
            for artifact in self._artifacts
            if artifact.produced_by_task in affected_tasks
        )
        changed = True
        while changed:
            changed = False
            for artifact in self._artifacts:
                if artifact.id not in invalidated and invalidated.intersection(artifact.derived_from):
                    invalidated.add(artifact.id)
                    changed = True

        for artifact in self._artifacts:
            if artifact.id not in invalidated or artifact.stale:
                continue
            artifact.stale = True
            self._emit(
                EventType.ARTIFACT_INVALIDATED,
                actor=actor,
                task_id=artifact.produced_by_task,
                summary=f"artifact '{artifact.name}' version {artifact.version} invalidated",
                payload={"artifact_id": artifact.id, "artifact_name": artifact.name},
            )
        return invalidated

    def _build_replacement_tasks(
        self,
        replacement_tasks: Iterable[Task] | None,
        affected_tasks: set[str],
    ) -> tuple[Task, ...]:
        old_by_id = {task.id: task for task in self._graph.topological_order()}
        source = (
            tuple(task.model_copy(deep=True) for task in replacement_tasks)
            if replacement_tasks is not None
            else tuple(task.model_copy(deep=True) for task in self._plan.tasks)
        )
        revised: list[Task] = []
        for task in source:
            old = old_by_id.get(task.id)
            if old is None:
                revised.append(task.model_copy(update={"state": TaskState.PENDING, "attempts": 0}))
            elif task.id in affected_tasks:
                revised.append(
                    task.model_copy(
                        update={"state": TaskState.STALE, "attempts": 0},
                        deep=True,
                    )
                )
            else:
                revised.append(
                    task.model_copy(
                        update={"state": old.state, "attempts": old.attempts},
                        deep=True,
                    )
                )
        return tuple(revised)

    def _finish(self, destination: RunState, reason: str, actor: Actor) -> None:
        if destination not in TERMINAL_RUN_STATES:
            raise EngineError(f"'{destination.value}' is not a terminal run state")
        self._transition_run(destination, actor=actor, reason=reason)
        completed = self._emit(
            EventType.RUN_COMPLETED,
            actor=actor,
            summary=reason,
            payload={"state": destination.value},
        )
        self._completed_at = completed.at

    def _acting_actor(self, task: Task, fallback: Actor) -> Actor:
        """Resolve an optional executor-owned audit identity for task events."""
        resolver = getattr(self._executor, "actor_for", None)
        if not callable(resolver):
            return fallback
        actor = resolver(task.model_copy(deep=True))
        if actor is None:
            return fallback
        if not isinstance(actor, Actor) or actor.kind is not ActorKind.AGENT:
            raise EngineError(
                f"executor returned an invalid acting agent for task '{task.id}'"
            )
        return actor

    def _emit(
        self,
        type: EventType,
        *,
        actor: Actor = SYSTEM_ACTOR,
        task_id: str | None = None,
        summary: str = "",
        payload: dict[str, object] | None = None,
    ) -> Event:
        return self._events.append(
            run_id=self.run_id,
            type=type,
            at=self._clock.iso(),
            actor=actor,
            task_id=task_id,
            summary=summary,
            payload=dict(payload or {}),
        )


Engine = OrchestrationEngine
