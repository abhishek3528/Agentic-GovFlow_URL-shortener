from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from orchestrator.clock import deterministic_pair
from orchestrator.contracts import ContextVersion, Plan, RunState, Stage, Task
from orchestrator.engine import OrchestrationEngine
from orchestrator.executor import DeterministicExecutor
from scenarios.runner import (
    EvidenceExporter,
    ScenarioError,
    ScenarioExecution,
    ScenarioRunner,
    ScenarioSpec,
)


def _execution(scenario_id: str) -> ScenarioExecution:
    clock, ids = deterministic_pair()
    context = ContextVersion(
        version=1,
        raw_requirement=f"run {scenario_id}",
        normalized_problem=f"execute {scenario_id} deterministically",
        acceptance_checks=("task completes",),
        created_at=clock.iso(),
    )
    plan = Plan(
        context_version=1,
        tasks=(
            Task(
                id=f"{scenario_id}-task",
                name="produce evidence",
                stage=Stage.TESTING,
                capability="qa",
                produces=(f"{scenario_id}-report",),
            ),
        ),
        created_at=clock.iso(),
    )
    engine = OrchestrationEngine(
        plan,
        DeterministicExecutor(),
        clock=clock,
        id_gen=ids,
        run_id=f"{scenario_id}-run",
    )
    assert engine.run() is RunState.SUCCEEDED
    return ScenarioExecution(engine=engine, context_versions=(context,))


def test_exporter_writes_indexed_reproducible_bundle(tmp_path: Path) -> None:
    exporter = EvidenceExporter(tmp_path)

    first = exporter.export("s-01", _execution("s-01"))
    bundle = tmp_path / first.run_id
    index = json.loads((bundle / "index.json").read_text(encoding="utf-8"))

    assert first.state is RunState.SUCCEEDED
    assert (bundle / "events.jsonl").is_file()
    assert json.loads((bundle / "result.json").read_text(encoding="utf-8"))["run_id"] == first.run_id
    assert index["schema_version"] == 1
    assert {entry["path"] for entry in index["files"]} == {
        "result.json",
        "context_versions.json",
        "plans.json",
        "graph.json",
        "artifacts.json",
        "decisions.json",
        "controls.json",
        "metrics.json",
        "events.jsonl",
    }
    for entry in index["files"]:
        content = (bundle / entry["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]

    snapshot = {path.name: path.read_bytes() for path in bundle.iterdir()}
    exporter.export("s-01", _execution("s-01"))
    assert {path.name: path.read_bytes() for path in bundle.iterdir()} == snapshot


def test_runner_executes_dependencies_in_order(tmp_path: Path) -> None:
    called: list[str] = []

    def execute(scenario_id: str):
        def callback(context):
            called.append(scenario_id)
            if scenario_id == "s-02":
                assert "s-01" in context.prior_results
            return _execution(scenario_id)

        return callback

    runner = ScenarioRunner(
        (
            ScenarioSpec("s-01", "first", execute("s-01")),
            ScenarioSpec(
                "s-02",
                "second",
                execute("s-02"),
                dependencies=("s-01",),
            ),
        ),
        evidence_root=tmp_path,
    )

    result = runner.run("s-02")

    assert result.scenario_id == "s-02"
    assert called == ["s-01", "s-02"]


def test_runner_rejects_cross_scenario_run_id_reuse(tmp_path: Path) -> None:
    def reused(_context):
        execution = _execution("s-01")
        return execution

    runner = ScenarioRunner(
        (
            ScenarioSpec("s-01", "first", reused),
            ScenarioSpec("s-02", "second", reused),
        ),
        evidence_root=tmp_path,
    )
    runner.run("s-01")

    with pytest.raises(ScenarioError, match="reused run id"):
        runner.run("s-02")


@pytest.mark.parametrize("run_id", ("../escape", "nested/path", ""))
def test_exporter_rejects_unsafe_run_id(tmp_path: Path, run_id: str) -> None:
    execution = _execution("s-01")
    execution.engine.run_id = run_id

    with pytest.raises(ValueError, match="safe path component"):
        EvidenceExporter(tmp_path).export("s-01", execution)
