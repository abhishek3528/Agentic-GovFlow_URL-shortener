"""Focused contract tests for Lane C policy, approval, recovery, and metrics."""

from __future__ import annotations

import pytest

from orchestrator.approval import ApprovalController, ApprovalError
from orchestrator.clock import deterministic_pair
from orchestrator.contracts import (
    Actor,
    ActorKind,
    Approval,
    EventType,
    Gate,
    GateKind,
    ImpactClass,
    Plan,
    PolicyEffect,
    RunState,
    Stage,
    Task,
    TaskState,
)
from orchestrator.engine import OrchestrationEngine
from orchestrator.executor import (
    DeterministicExecutor,
    ScriptedFailureExecutor,
    TaskOutput,
)
from orchestrator.policy import (
    CHANGE_CONTROL_POLICY,
    EVIDENCE_RETENTION_POLICY,
    PRIVACY_POLICY,
    URL_SAFETY_POLICY,
    PolicyEngine,
)
from orchestrator.recovery import RecoveryAction, RecoveryController


def _plan(task: Task) -> Plan:
    return Plan(
        revision=1,
        context_version=1,
        tasks=(task,),
        created_at="2026-01-01T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("policy_id", "allow_context", "deny_context"),
    [
        (
            URL_SAFETY_POLICY,
            {"destination": "https://example.test/path"},
            {"destination": "javascript:alert(1)"},
        ),
        (
            PRIVACY_POLICY,
            {"retained_fields": ("click_count", "day_bucket")},
            {"retained_fields": ("click_count", "client_ip")},
        ),
        (
            CHANGE_CONTROL_POLICY,
            {"is_breaking": True, "declared_impact": ImpactClass.BREAKING},
            {"is_breaking": True, "declared_impact": ImpactClass.LOW},
        ),
        (
            EVIDENCE_RETENTION_POLICY,
            {"retention_days": 30, "append_only": True},
            {"retention_days": 7, "append_only": True},
        ),
    ],
)
def test_each_named_policy_has_allow_and_deny_paths(
    policy_id: str,
    allow_context: dict[str, object],
    deny_context: dict[str, object],
) -> None:
    clock, _ = deterministic_pair()
    policies = PolicyEngine(clock=clock)

    allowed = policies.evaluate(policy_id, "task-controls", allow_context)
    denied = policies.evaluate(policy_id, "task-controls", deny_context)

    assert allowed.effect is PolicyEffect.ALLOW
    assert denied.effect is PolicyEffect.DENY
    assert allowed.policy_id == denied.policy_id == policy_id


def test_unknown_policy_fails_closed() -> None:
    decision = PolicyEngine().evaluate("not-registered", "task-1", {})
    assert decision.denied
    assert "failed closed" in decision.reason


def test_required_policy_decision_is_recorded_and_enforced() -> None:
    task = Task(
        id="task-policy",
        name="validate destination",
        stage=Stage.TESTING,
        capability="qa",
    )
    clock, ids = deterministic_pair()
    engine = OrchestrationEngine(
        _plan(task),
        DeterministicExecutor(),
        clock=clock,
        id_gen=ids,
        required_policies={task.id: (URL_SAFETY_POLICY,)},
    )
    engine.record_policy_decision(
        PolicyEngine(clock=clock).evaluate_url(task.id, "https://example.test")
    )

    assert engine.run() is RunState.SUCCEEDED
    assert any(event.type is EventType.POLICY_EVALUATED for event in engine.events)
    assert engine.metrics.policy_denials == 0


def test_approval_controller_correlates_one_decision_to_one_request() -> None:
    controller = ApprovalController()
    human = Actor(kind=ActorKind.HUMAN, id="reviewer:alex")
    approval = Approval(
        id="approval-1",
        task_id="task-release",
        granted=True,
        actor=human,
        rationale="release evidence reviewed",
        decided_at="2026-01-01T00:00:00+00:00",
    )
    with pytest.raises(ApprovalError, match="no pending"):
        controller.decide(approval)
    controller.request(approval.task_id)
    assert controller.decide(approval) == approval


def test_human_approval_resumes_high_impact_task() -> None:
    task = Task(
        id="task-release",
        name="release",
        stage=Stage.RELEASE_READINESS,
        capability="release-manager",
        impact=ImpactClass.HIGH,
    )
    clock, ids = deterministic_pair()
    engine = OrchestrationEngine(
        _plan(task), DeterministicExecutor(), clock=clock, id_gen=ids
    )

    assert engine.run() is RunState.AWAITING_APPROVAL
    approval = Approval(
        id=ids.next_id("approval"),
        task_id=task.id,
        granted=True,
        actor=Actor(kind=ActorKind.HUMAN, id="reviewer:alex"),
        rationale="quality gate passed",
        decided_at=clock.iso(),
    )

    assert engine.decide_approval(approval) is RunState.SUCCEEDED
    assert engine.task(task.id).state is TaskState.SUCCEEDED
    assert [event.type for event in engine.events].count(EventType.APPROVAL_REQUESTED) == 1
    assert [event.type for event in engine.events].count(EventType.APPROVAL_DECIDED) == 1


def test_transient_failure_recovers_within_retry_budget() -> None:
    task = Task(
        id="task-flaky",
        name="flaky validation",
        stage=Stage.TESTING,
        capability="qa",
        retry_budget=1,
    )
    executor = ScriptedFailureExecutor(
        DeterministicExecutor(), failures={task.id: 1}
    )
    clock, ids = deterministic_pair()
    engine = OrchestrationEngine(_plan(task), executor, clock=clock, id_gen=ids)

    assert engine.run() is RunState.SUCCEEDED
    assert engine.task(task.id).attempts == 2
    assert engine.metrics.retry_count == 1
    assert engine.metrics.mttr_seconds is not None


def test_non_transient_failure_compensates_then_stops_safely() -> None:
    task = Task(
        id="task-doomed",
        name="persistent validation",
        stage=Stage.TESTING,
        capability="qa",
        retry_budget=3,
        compensation="restore-test-fixture",
    )
    executor = ScriptedFailureExecutor(
        DeterministicExecutor(), persistent=frozenset({task.id})
    )
    recovery = RecoveryController(
        {"restore-test-fixture": lambda task, reason: (True, "fixture restored")}
    )
    clock, ids = deterministic_pair()
    engine = OrchestrationEngine(
        _plan(task),
        executor,
        recovery_controller=recovery,
        clock=clock,
        id_gen=ids,
    )

    assert engine.run() is RunState.SAFE_STOPPED
    assert engine.task(task.id).state is TaskState.COMPENSATED
    assert engine.task(task.id).attempts == 1
    assert engine.metrics.retry_count == 0
    assert engine.metrics.rollback_count == 1
    assert EventType.SAFE_STOP_TRIGGERED in {event.type for event in engine.events}


def test_fallback_is_selected_only_after_retry_budget_is_exhausted() -> None:
    task = Task(
        id="task-fallback-selection",
        name="fallback selection",
        stage=Stage.TESTING,
        capability="qa",
        retry_budget=1,
        attempts=1,
        compensation="alternate-validator",
    )
    fallback = lambda task, inputs: TaskOutput(summary="alternate succeeded")
    recovery = RecoveryController(fallbacks={"alternate-validator": fallback})

    assert recovery.decide(task, transient=True).action is RecoveryAction.RETRY
    exhausted = task.model_copy(update={"attempts": 2})
    assert recovery.decide(exhausted, transient=True).action is RecoveryAction.FALLBACK


def test_registered_fallback_precedes_compensation_or_safe_stop() -> None:
    fallback = lambda task, inputs: TaskOutput(summary="alternate succeeded")
    compensable = Task(
        id="task-with-recovery",
        name="recoverable task",
        stage=Stage.TESTING,
        capability="qa",
        compensation="restore-fixture",
    )
    unrecoverable = compensable.model_copy(
        update={"id": "task-without-recovery", "compensation": None}
    )

    with_fallback = RecoveryController(
        fallbacks={"restore-fixture": fallback}
    ).decide(compensable, transient=False)
    without_fallback = RecoveryController().decide(compensable, transient=False)
    without_recovery = RecoveryController().decide(unrecoverable, transient=False)

    assert with_fallback.action is RecoveryAction.FALLBACK
    assert without_fallback.action is RecoveryAction.COMPENSATE
    assert without_recovery.action is RecoveryAction.SAFE_STOP


def test_successful_fallback_uses_normal_artifact_and_exit_gate_path() -> None:
    gate = Gate(
        id="fallback-output-reviewed",
        kind=GateKind.EXIT,
        description="fallback output was reviewed",
    )
    task = Task(
        id="task-fallback-success",
        name="fallback validation",
        stage=Stage.TESTING,
        capability="qa",
        produces=("fallback-report",),
        exit_gates=(gate,),
        retry_budget=1,
        compensation="alternate-validator",
    )
    executor = ScriptedFailureExecutor(
        DeterministicExecutor(), failures={task.id: 2}
    )
    fallback_calls: list[int] = []
    gate_artifacts: list[set[str]] = []

    def fallback(fallback_task: Task, inputs: dict[str, str]) -> TaskOutput:
        fallback_calls.append(fallback_task.attempts)
        return TaskOutput(
            summary="alternate validator succeeded",
            artifacts={"fallback-report": "fallback evidence"},
            validation_results={"fallback-check": True},
        )

    def evaluate_gate(gate, task, artifacts):
        gate_artifacts.append(set(artifacts))
        return True, "fallback evidence reviewed"

    clock, ids = deterministic_pair()
    engine = OrchestrationEngine(
        _plan(task),
        executor,
        recovery_controller=RecoveryController(
            fallbacks={"alternate-validator": fallback}
        ),
        gate_evaluator=evaluate_gate,
        clock=clock,
        id_gen=ids,
    )

    assert engine.run() is RunState.SUCCEEDED
    assert engine.task(task.id).state is TaskState.SUCCEEDED
    assert engine.task(task.id).attempts == 2
    assert fallback_calls == [2]
    assert gate_artifacts == [{"fallback-report"}]
    assert [artifact.name for artifact in engine.artifacts] == ["fallback-report"]

    retry = next(event for event in engine.events if event.type is EventType.RETRY_ATTEMPTED)
    fallback_event = next(
        event
        for event in engine.events
        if event.type is EventType.DECISION_RECORDED
        and event.payload.get("recovery_action") == "fallback"
    )
    assert retry.seq < fallback_event.seq
    assert fallback_event.payload == {
        "recovery_action": "fallback",
        "handler": "alternate-validator",
        "executed": True,
        "succeeded": True,
        "reason": "alternate validator succeeded",
        "failure_reason": "injected transient failure in 'task-fallback-success'",
    }
    assert any(
        event.type is EventType.ARTIFACT_PRODUCED and event.task_id == task.id
        for event in engine.events
    )
    assert any(
        event.type is EventType.GATE_EVALUATED
        and event.task_id == task.id
        and event.payload["passed"] is True
        for event in engine.events
    )


def test_fallback_cannot_bypass_a_denied_exit_gate() -> None:
    task = Task(
        id="task-fallback-denied",
        name="denied fallback",
        stage=Stage.TESTING,
        capability="qa",
        produces=("report",),
        exit_gates=(
            Gate(id="review", kind=GateKind.EXIT, description="review output"),
        ),
        compensation="alternate-validator",
    )
    executor = ScriptedFailureExecutor(
        DeterministicExecutor(), persistent=frozenset({task.id})
    )
    recovery = RecoveryController(
        fallbacks={
            "alternate-validator": lambda task, inputs: TaskOutput(
                summary="fallback work completed",
                artifacts={"report": "unapproved evidence"},
            )
        }
    )
    engine = OrchestrationEngine(
        _plan(task),
        executor,
        recovery_controller=recovery,
        gate_evaluator=lambda gate, task, artifacts: (False, "review denied"),
    )

    assert engine.run() is RunState.FAILED
    assert engine.task(task.id).state is TaskState.BLOCKED
    assert len(engine.artifacts) == 1
    assert any(
        event.type is EventType.GATE_EVALUATED
        and event.payload["passed"] is False
        for event in engine.events
    )
    assert not any(
        event.type is EventType.COMPENSATION_EXECUTED for event in engine.events
    )


def test_failed_fallback_falls_through_to_compensation_and_safe_stop() -> None:
    task = Task(
        id="task-fallback-fails",
        name="failed fallback",
        stage=Stage.TESTING,
        capability="qa",
        compensation="restore-fixture",
    )
    executor = ScriptedFailureExecutor(
        DeterministicExecutor(), persistent=frozenset({task.id})
    )
    recovery = RecoveryController(
        compensations={"restore-fixture": lambda task, reason: (True, "restored")},
        fallbacks={
            "restore-fixture": lambda task, inputs: TaskOutput(
                succeeded=False,
                failure_reason="alternate validator failed",
            )
        },
    )
    engine = OrchestrationEngine(
        _plan(task), executor, recovery_controller=recovery
    )

    assert engine.run() is RunState.SAFE_STOPPED
    assert engine.task(task.id).state is TaskState.COMPENSATED
    fallback_event = next(
        event
        for event in engine.events
        if event.type is EventType.DECISION_RECORDED
        and event.payload.get("recovery_action") == "fallback"
    )
    compensation_event = next(
        event for event in engine.events if event.type is EventType.COMPENSATION_EXECUTED
    )
    assert fallback_event.seq < compensation_event.seq
    assert fallback_event.payload["executed"] is True
    assert fallback_event.payload["succeeded"] is False
    assert fallback_event.payload["reason"] == "alternate validator failed"
    assert compensation_event.payload["succeeded"] is True
    assert EventType.SAFE_STOP_TRIGGERED in {event.type for event in engine.events}


@pytest.mark.parametrize(
    ("handler", "expected_reason"),
    [
        (
            lambda task, inputs: (_ for _ in ()).throw(RuntimeError("boom")),
            "fallback handler failed closed: RuntimeError: boom",
        ),
        (
            lambda task, inputs: object(),
            "fallback handler failed closed: TypeError: fallback handler must return TaskOutput",
        ),
    ],
)
def test_invalid_fallback_handlers_fail_closed(handler, expected_reason: str) -> None:
    task = Task(
        id="task-invalid-fallback",
        name="invalid fallback",
        stage=Stage.TESTING,
        capability="qa",
    )
    executor = ScriptedFailureExecutor(
        DeterministicExecutor(), persistent=frozenset({task.id})
    )
    recovery = RecoveryController(fallbacks={task.id: handler})
    engine = OrchestrationEngine(
        _plan(task), executor, recovery_controller=recovery
    )

    assert engine.run() is RunState.SAFE_STOPPED
    assert engine.task(task.id).state is TaskState.FAILED
    fallback_event = next(
        event
        for event in engine.events
        if event.type is EventType.DECISION_RECORDED
        and event.payload.get("recovery_action") == "fallback"
    )
    assert fallback_event.payload["handler"] == task.id
    assert fallback_event.payload["executed"] is True
    assert fallback_event.payload["succeeded"] is False
    assert fallback_event.payload["reason"] == expected_reason
    assert EventType.SAFE_STOP_TRIGGERED in {event.type for event in engine.events}
