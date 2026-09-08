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
    ImpactClass,
    Plan,
    PolicyEffect,
    RunState,
    Stage,
    Task,
    TaskState,
)
from orchestrator.engine import OrchestrationEngine
from orchestrator.executor import DeterministicExecutor, ScriptedFailureExecutor
from orchestrator.policy import (
    CHANGE_CONTROL_POLICY,
    EVIDENCE_RETENTION_POLICY,
    PRIVACY_POLICY,
    URL_SAFETY_POLICY,
    PolicyEngine,
)
from orchestrator.recovery import RecoveryController


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
