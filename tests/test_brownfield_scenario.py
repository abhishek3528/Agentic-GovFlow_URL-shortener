"""Acceptance checks for the replayable S-02 brownfield scenario."""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.contracts import EventType, RunState, TaskState
from orchestrator.recovery import compute_run_metrics
from scenarios.brownfield import COMPENSATION_NAME, RUN_ID, execute
from scenarios.greenfield import execute as execute_greenfield
from scenarios.runner import EvidenceExporter, ScenarioContext


WORKSPACE = Path(__file__).resolve().parents[1]


def _execute(tmp_path: Path):
    greenfield = execute_greenfield(
        ScenarioContext(workspace=WORKSPACE, evidence_root=tmp_path, prior_results={})
    )
    baseline = EvidenceExporter(tmp_path).export("s-01", greenfield)
    return execute(
        ScenarioContext(
            workspace=WORKSPACE,
            evidence_root=tmp_path,
            prior_results={"s-01": baseline},
        )
    )


def _event_index(engine, event_type: EventType, task_id: str) -> int:
    return next(
        index
        for index, event in enumerate(engine.events)
        if event.type is event_type and event.task_id == task_id
    )


def test_s02_impact_and_red_fixture_gate_change_planning(tmp_path: Path) -> None:
    engine = _execute(tmp_path).engine

    impact_done = _event_index(
        engine, EventType.ARTIFACT_PRODUCED, "analyze-baseline-impact"
    )
    red_done = _event_index(
        engine, EventType.ARTIFACT_PRODUCED, "reproduce-collision-idempotency-defect"
    )
    planning_started = next(
        index
        for index, event in enumerate(engine.events)
        if event.type is EventType.TASK_STATE_CHANGED
        and event.task_id == "plan-gated-fix"
        and event.payload.get("to") == TaskState.RUNNING.value
    )
    assert impact_done < red_done < planning_started

    planning_gate = next(
        event
        for event in engine.events
        if event.type is EventType.GATE_EVALUATED
        and event.task_id == "plan-gated-fix"
        and event.payload.get("gate_id") == "impact-and-red-required-before-planning"
    )
    assert planning_gate.payload["passed"] is True


def test_s02_red_to_green_fork_join_and_gated_fix(tmp_path: Path) -> None:
    engine = _execute(tmp_path).engine
    artifacts = {artifact.name: artifact for artifact in engine.artifacts}

    assert engine.task("apply-collision-idempotency-fix").state is TaskState.SUCCEEDED
    assert engine.approvals[0].task_id == "apply-collision-idempotency-fix"
    assert engine.policy_decisions[0].policy_id == "change-control"
    assert engine.policy_decisions[0].denied is False
    assert set(artifacts["before_after_proof"].derived_from) == {
        artifacts["red_regression"].id,
        artifacts["green_regression"].id,
        artifacts["regression_report"].id,
        artifacts["change_documentation"].id,
    }

    red = next(
        event
        for event in engine.events
        if event.type is EventType.VALIDATION_EXECUTED
        and event.task_id == "reproduce-collision-idempotency-defect"
    )
    green = [
        event
        for event in engine.events
        if event.type is EventType.VALIDATION_EXECUTED
        and event.task_id == "validate-fixed-behavior"
    ]
    assert red.payload["passed"] is True
    assert green and all(event.payload["passed"] is True for event in green)


def test_s02_exhausts_retry_compensates_and_safe_stops_with_metrics(tmp_path: Path) -> None:
    engine = _execute(tmp_path).engine

    assert engine.run_id == RUN_ID
    assert engine.run_state is RunState.SAFE_STOPPED
    assert engine.task("verify-release-candidate").attempts == 2
    assert engine.task("verify-release-candidate").state is TaskState.COMPENSATED

    compensation = next(
        event for event in engine.events if event.type is EventType.COMPENSATION_EXECUTED
    )
    assert compensation.actor.id == "agent:release-engineer"
    assert compensation.payload["action"] == COMPENSATION_NAME
    assert compensation.payload["executed"] is True
    assert compensation.payload["succeeded"] is True
    assert "restored" in compensation.payload["reason"]
    assert _event_index(
        engine, EventType.COMPENSATION_EXECUTED, "verify-release-candidate"
    ) < _event_index(engine, EventType.SAFE_STOP_TRIGGERED, "verify-release-candidate")

    assert engine.metrics == compute_run_metrics(engine.events, run_id=RUN_ID)
    assert engine.metrics.retry_count == 1
    assert engine.metrics.rollback_count == 1
    assert engine.metrics.rollback_rate > 0
    assert engine.metrics.mttr_seconds == 5.0


def test_s02_exports_safe_stopped_red_to_green_evidence(tmp_path: Path) -> None:
    result = EvidenceExporter(tmp_path).export("s-02", _execute(tmp_path))
    bundle = tmp_path / RUN_ID

    assert result.state is RunState.SAFE_STOPPED
    metrics = json.loads((bundle / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["retry_count"] == 1
    assert metrics["rollback_count"] == 1
    assert metrics["mttr_seconds"] == 5.0

    events = [
        json.loads(line)
        for line in (bundle / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["type"] == "compensation_executed" for event in events)
    assert events[-1]["type"] == "run_completed"
    assert events[-1]["payload"]["state"] == "safe_stopped"
