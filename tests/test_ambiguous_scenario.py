"""Acceptance checks for S-03 ambiguity handling and governed re-planning."""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.contracts import ActorKind, EventType, PolicyEffect, RunState, TaskState
from orchestrator.recovery import compute_run_metrics
from scenarios.ambiguous import AMBIGUITIES, RUN_ID, execute
from scenarios.brownfield import execute as execute_brownfield
from scenarios.greenfield import execute as execute_greenfield
from scenarios.runner import EvidenceExporter, ScenarioContext


WORKSPACE = Path(__file__).resolve().parents[1]


def _execute(tmp_path: Path):
    exporter = EvidenceExporter(tmp_path)
    greenfield = execute_greenfield(
        ScenarioContext(workspace=WORKSPACE, evidence_root=tmp_path, prior_results={})
    )
    s01 = exporter.export("s-01", greenfield)
    brownfield = execute_brownfield(
        ScenarioContext(
            workspace=WORKSPACE,
            evidence_root=tmp_path,
            prior_results={"s-01": s01},
        )
    )
    s02 = exporter.export("s-02", brownfield)
    return execute(
        ScenarioContext(
            workspace=WORKSPACE,
            evidence_root=tmp_path,
            prior_results={"s-01": s01, "s-02": s02},
        )
    )


def _transition_index(engine, task_id: str, destination: TaskState, revision: int) -> int:
    return next(
        index
        for index, event in enumerate(engine.events)
        if event.type is EventType.TASK_STATE_CHANGED
        and event.task_id == task_id
        and event.payload.get("to") == destination.value
        and event.payload.get("plan_revision") == revision
    )


def test_s03_surfaces_ambiguities_and_pauses_before_v1_normalization(tmp_path: Path) -> None:
    engine = _execute(tmp_path).engine

    assert {item.id for item in AMBIGUITIES} == {
        "analytics-dimension",
        "privacy-retention",
        "acceptance-threshold",
        "scope-boundary",
    }
    assert all(item.proposed_assumption and item.consequence_if_wrong for item in AMBIGUITIES)

    requested = next(
        index
        for index, event in enumerate(engine.events)
        if event.type is EventType.APPROVAL_REQUESTED
        and event.task_id == "normalize-requirement-v1"
    )
    decided = next(
        index
        for index, event in enumerate(engine.events)
        if event.type is EventType.APPROVAL_DECIDED
        and event.task_id == "normalize-requirement-v1"
    )
    normalized = next(
        index
        for index, event in enumerate(engine.events)
        if event.type is EventType.ARTIFACT_PRODUCED
        and event.task_id == "normalize-requirement-v1"
    )
    assert requested < decided < normalized
    assert engine.events[decided].actor.kind is ActorKind.HUMAN


def test_s03_parallel_planning_joins_before_human_clarification(tmp_path: Path) -> None:
    engine = _execute(tmp_path).engine

    tests_ready = _transition_index(engine, "plan-analytics-tests", TaskState.READY, 1)
    docs_ready = _transition_index(engine, "plan-analytics-documentation", TaskState.READY, 1)
    tests_running = _transition_index(engine, "plan-analytics-tests", TaskState.RUNNING, 1)
    join_running = _transition_index(engine, "join-analytics-plans", TaskState.RUNNING, 1)
    assert engine.events[tests_running].actor.id == "agent:quality-engineer"
    assert engine.events[join_running].actor.id == "agent:delivery-lead"
    assert tests_ready < tests_running
    assert docs_ready < tests_running
    assert join_running > _transition_index(
        engine, "plan-analytics-tests", TaskState.SUCCEEDED, 1
    )
    assert join_running > _transition_index(
        engine, "plan-analytics-documentation", TaskState.SUCCEEDED, 1
    )


def test_s03_replan_preserves_v1_and_selectively_invalidates_analytics(tmp_path: Path) -> None:
    engine = _execute(tmp_path).engine

    assert engine.run_state is RunState.SUCCEEDED
    assert [context.version for context in engine.context_versions] == [1, 2]
    assert engine.context_versions[1].supersedes == 1
    assert [plan.revision for plan in engine.plans] == [1, 2]
    assert engine.plans[1].supersedes == 1

    artifacts_by_name: dict[str, list] = {}
    for artifact in engine.artifacts:
        artifacts_by_name.setdefault(artifact.name, []).append(artifact)
    assert [(item.version, item.stale) for item in artifacts_by_name["analytics_design"]] == [
        (1, True),
        (2, False),
    ]
    assert [(item.version, item.stale) for item in artifacts_by_name["analytics_test_plan"]] == [
        (1, True),
        (2, False),
    ]
    assert len(artifacts_by_name["core_scope_assurance"]) == 1
    assert artifacts_by_name["core_scope_assurance"][0].stale is False
    assert sum(artifact.stale for artifact in engine.artifacts) == 5
    assert sum(not artifact.stale for artifact in engine.artifacts) == 14

    revised = next(event for event in engine.events if event.type is EventType.PLAN_REVISED)
    assert "design-analytics-change" in revised.payload["affected_task_ids"]
    assert "preserve-core-scope" not in revised.payload["affected_task_ids"]
    assert any(event.type is EventType.ARTIFACT_INVALIDATED for event in engine.events)
    assert engine.metrics.replans == 1


def test_s03_reapplies_policies_validation_and_final_human_quality_gate(tmp_path: Path) -> None:
    engine = _execute(tmp_path).engine

    v1_denial = next(
        event
        for event in engine.events
        if event.type is EventType.POLICY_EVALUATED
        and event.task_id == "implement-coarse-analytics"
        and event.payload.get("plan_revision") == 1
    )
    assert v1_denial.payload["effect"] == PolicyEffect.DENY.value

    v2_policy_events = [
        event
        for event in engine.events
        if event.type is EventType.POLICY_EVALUATED
        and event.payload.get("plan_revision") == 2
    ]
    assert {event.payload["policy_id"] for event in v2_policy_events} == {
        "privacy",
        "change-control",
        "evidence-retention",
    }
    assert all(event.payload["effect"] == PolicyEffect.ALLOW.value for event in v2_policy_events)

    validations = [
        event
        for event in engine.events
        if event.type is EventType.VALIDATION_EXECUTED
        and event.task_id == "validate-coarse-analytics"
    ]
    assert validations and all(event.payload["passed"] is True for event in validations)
    final_approval = next(
        event
        for event in engine.events
        if event.type is EventType.APPROVAL_DECIDED
        and event.task_id == "final-quality-approval"
    )
    assert final_approval.actor.kind is ActorKind.HUMAN
    assert final_approval.payload["plan_revision"] == 2
    assert engine.metrics == compute_run_metrics(engine.events, run_id=RUN_ID)


def test_s03_exports_both_contexts_and_append_only_replan_evidence(tmp_path: Path) -> None:
    result = EvidenceExporter(tmp_path).export("s-03", _execute(tmp_path))
    bundle = tmp_path / RUN_ID

    assert result.state is RunState.SUCCEEDED
    contexts = json.loads((bundle / "context_versions.json").read_text(encoding="utf-8"))
    plans = json.loads((bundle / "plans.json").read_text(encoding="utf-8"))
    artifacts = json.loads((bundle / "artifacts.json").read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in (bundle / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [item["version"] for item in contexts] == [1, 2]
    assert [item["revision"] for item in plans] == [1, 2]
    assert any(item["name"] == "analytics_design" and item["stale"] for item in artifacts)
    assert [event["seq"] for event in events] == list(range(len(events)))
    assert any(event["type"] == EventType.REPLAN_TRIGGERED.value for event in events)
    assert events[-1]["type"] == EventType.RUN_COMPLETED.value
