"""Acceptance checks for the replayable S-01 greenfield scenario."""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.clock import deterministic_pair
from orchestrator.contracts import ActorKind, EventType, PolicyEffect, RunState, Stage, TaskState
from orchestrator.engine import OrchestrationEngine
from orchestrator.executor import DeterministicExecutor
from orchestrator.recovery import compute_run_metrics
from scenarios.greenfield import RUN_ID, _gate_evaluator, build_plan, execute
from scenarios.runner import EvidenceExporter, ScenarioContext


def _execute(tmp_path: Path):
    context = ScenarioContext(
        workspace=Path(__file__).resolve().parents[1],
        evidence_root=tmp_path,
        prior_results={},
    )
    return execute(context)


def _transition_index(engine, task_id: str, destination: TaskState) -> int:
    return next(
        index
        for index, event in enumerate(engine.events)
        if event.type is EventType.TASK_STATE_CHANGED
        and event.task_id == task_id
        and event.payload.get("to") == destination.value
    )


def test_s01_executes_full_sdlc_fork_join_and_human_release_gate(tmp_path: Path) -> None:
    scratch_root = Path(__file__).resolve().parents[1] / "tmp"
    scratch_before = {path.name for path in scratch_root.glob("s01-smoke-*")}
    execution = _execute(tmp_path)
    engine = execution.engine
    assert {path.name for path in scratch_root.glob("s01-smoke-*")} == scratch_before

    assert engine.run_id == RUN_ID
    assert engine.run_state is RunState.SUCCEEDED
    assert {task.stage for task in engine.current_plan.tasks} == set(Stage)
    assert all(task.state is TaskState.SUCCEEDED for task in engine.current_plan.tasks)

    # Both fork nodes become READY before either starts, and the join starts
    # only after both branch outputs have passed their exit gates.
    core_ready = _transition_index(engine, "implement-core", TaskState.READY)
    analytics_ready = _transition_index(
        engine, "implement-analytics-reliability", TaskState.READY
    )
    core_running = _transition_index(engine, "implement-core", TaskState.RUNNING)
    join_running = _transition_index(engine, "integrated-validation", TaskState.RUNNING)
    assert core_ready < core_running
    assert analytics_ready < core_running
    assert join_running > _transition_index(engine, "implement-core", TaskState.SUCCEEDED)
    assert join_running > _transition_index(
        engine, "implement-analytics-reliability", TaskState.SUCCEEDED
    )

    artifacts = {artifact.name: artifact for artifact in engine.artifacts}
    assert set(artifacts["integrated_validation"].derived_from) == {
        artifacts["core_implementation"].id,
        artifacts["analytics_reliability_implementation"].id,
    }
    assert "smoke_result" in artifacts
    assert any(
        event.type is EventType.VALIDATION_EXECUTED
        and event.task_id == "integrated-validation"
        and event.payload.get("name") == "analytics"
        and event.payload.get("passed") is True
        for event in engine.events
    )

    requested = next(
        event for event in engine.events if event.type is EventType.APPROVAL_REQUESTED
    )
    decided = next(
        event for event in engine.events if event.type is EventType.APPROVAL_DECIDED
    )
    assert requested.task_id == decided.task_id == "release-readiness"
    assert decided.actor.kind is ActorKind.HUMAN
    assert requested.seq < decided.seq < _transition_index(
        engine, "release-readiness", TaskState.SUCCEEDED
    )


def test_s01_records_denial_recovery_lineage_and_event_derived_metrics(tmp_path: Path) -> None:
    engine = _execute(tmp_path).engine

    unsafe = next(
        decision
        for decision in engine.policy_decisions
        if decision.effect is PolicyEffect.DENY
    )
    allowed = next(
        decision
        for decision in engine.policy_decisions
        if decision.effect is PolicyEffect.ALLOW
    )
    assert unsafe.policy_id == allowed.policy_id == "url-safety"
    assert "prohibited" in unsafe.reason
    assert engine.task("integrated-validation").attempts == 2
    assert engine.metrics.retry_count == 1
    assert engine.metrics.policy_denials == 1
    assert engine.metrics.approvals_requested == 1
    assert engine.metrics == compute_run_metrics(engine.events, run_id=RUN_ID)
    assert len(engine.context_versions) == 1
    assert len(engine.decisions) == 1
    assert any(event.type is EventType.CONTEXT_VERSION_CREATED for event in engine.events)
    assert any(event.type is EventType.DECISION_RECORDED for event in engine.events)


def test_s01_evidence_is_generated_with_terminal_task_state(tmp_path: Path) -> None:
    result = EvidenceExporter(tmp_path).export("s-01", _execute(tmp_path))
    bundle = tmp_path / RUN_ID

    assert result.state is RunState.SUCCEEDED
    persisted = json.loads((bundle / "result.json").read_text(encoding="utf-8"))
    assert {task["state"] for task in persisted["plans"][-1]["tasks"]} == {"succeeded"}
    controls = json.loads((bundle / "controls.json").read_text(encoding="utf-8"))
    assert any(item["effect"] == "deny" for item in controls["policy_decisions"])
    assert controls["approvals"][0]["actor"]["kind"] == "human"


def test_s01_incomplete_requirement_fails_closed_at_entry() -> None:
    clock, ids = deterministic_pair()
    engine = OrchestrationEngine(
        build_plan(created_at=clock.iso()),
        DeterministicExecutor(),
        clock=clock,
        id_gen=ids,
        gate_evaluator=_gate_evaluator("create a short link"),
    )

    assert engine.run() is RunState.FAILED
    assert engine.task("normalize-requirement").state is TaskState.BLOCKED
    denial = next(
        event
        for event in engine.events
        if event.type is EventType.GATE_EVALUATED
        and event.task_id == "normalize-requirement"
    )
    assert denial.payload["passed"] is False
    assert "missing" in denial.payload["reason"]
