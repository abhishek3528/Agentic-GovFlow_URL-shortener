"""S-01: deterministic greenfield URL-shortener baseline.

The scenario exercises the real governance engine around the existing product
slice.  Its executor performs bounded, credential-free work; the engine alone
owns scheduling, policy enforcement, retries, approval, events, and artifacts.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from orchestrator.clock import deterministic_pair
from orchestrator.contracts import (
    Actor,
    ActorKind,
    Approval,
    Artifact,
    ContextVersion,
    Decision,
    Gate,
    GateKind,
    ImpactClass,
    Plan,
    RunState,
    Stage,
    Task,
    TaskState,
)
from orchestrator.engine import OrchestrationEngine
from orchestrator.executor import DeterministicExecutor, ScriptedFailureExecutor, TaskOutput
from orchestrator.policy import URL_SAFETY_POLICY, PolicyEngine
from scenarios.runner import ScenarioContext, ScenarioExecution, ScenarioSpec


SCENARIO_ID = "s-01"
RUN_ID = "s-01-greenfield"

AGENT = Actor(kind=ActorKind.AGENT, id="agent:s01-orchestrator")
HUMAN_RELEASE_MANAGER = Actor(
    kind=ActorKind.HUMAN,
    id="reviewer:s01-release-manager",
)

RAW_REQUIREMENT = (
    "Establish a URL-shortener baseline that creates and redirects short URLs, "
    "reports basic privacy-conscious analytics, exposes health and readiness, "
    "publishes OpenAPI, and validates collision and idempotency behavior."
)


def _gate(gate_id: str, kind: GateKind, description: str) -> Gate:
    return Gate(id=gate_id, kind=kind, description=description)


def build_plan(*, created_at: str) -> Plan:
    """Build the visible requirement -> design -> fork/join -> release DAG."""
    requirement_entry = _gate(
        "requirement-structure",
        GateKind.ENTRY,
        "The request names the required product outcomes.",
    )
    outputs_present = _gate(
        "declared-outputs-present",
        GateKind.EXIT,
        "Every declared output is present before task success.",
    )
    inputs_fresh = _gate(
        "declared-inputs-fresh",
        GateKind.ENTRY,
        "Every declared input exists and is fresh.",
    )
    release_evidence = _gate(
        "release-evidence-complete",
        GateKind.ENTRY,
        "Integrated validation, documentation, and API contract are present.",
    )

    normalize = Task(
        id="normalize-requirement",
        name="Normalize greenfield requirement",
        stage=Stage.REQUIREMENTS,
        capability="business-analyst",
        produces=("normalized_requirement",),
        entry_gates=(requirement_entry,),
        exit_gates=(outputs_present,),
    )
    design = Task(
        id="design-baseline",
        name="Define service and API design",
        stage=Stage.DESIGN,
        capability="solution-architect",
        depends_on=(normalize.id,),
        consumes=("normalized_requirement",),
        produces=("service_design", "api_contract"),
        entry_gates=(inputs_fresh,),
        exit_gates=(outputs_present,),
    )
    core = Task(
        id="implement-core",
        name="Implement create and redirect path",
        stage=Stage.IMPLEMENTATION,
        capability="backend-engineer",
        depends_on=(design.id,),
        consumes=("service_design", "api_contract"),
        produces=("core_implementation",),
        entry_gates=(inputs_fresh,),
        exit_gates=(outputs_present,),
    )
    analytics = Task(
        id="implement-analytics-reliability",
        name="Implement analytics and health path",
        stage=Stage.IMPLEMENTATION,
        capability="reliability-engineer",
        depends_on=(design.id,),
        consumes=("service_design", "api_contract"),
        produces=("analytics_reliability_implementation",),
        entry_gates=(inputs_fresh,),
        exit_gates=(outputs_present,),
    )
    validation = Task(
        id="integrated-validation",
        name="Run unit, integration, contract, and smoke validation",
        stage=Stage.TESTING,
        capability="quality-engineer",
        depends_on=(core.id, analytics.id),
        consumes=("core_implementation", "analytics_reliability_implementation"),
        produces=("integrated_validation", "smoke_result"),
        entry_gates=(inputs_fresh,),
        exit_gates=(outputs_present,),
        retry_budget=1,
    )
    documentation = Task(
        id="document-baseline",
        name="Publish baseline usage and validation references",
        stage=Stage.DOCUMENTATION,
        capability="technical-writer",
        depends_on=(design.id, validation.id),
        consumes=("api_contract", "integrated_validation"),
        produces=("operator_guide",),
        entry_gates=(inputs_fresh,),
        exit_gates=(outputs_present,),
    )
    release = Task(
        id="release-readiness",
        name="Human release-readiness review",
        stage=Stage.RELEASE_READINESS,
        capability="release-manager",
        depends_on=(validation.id, documentation.id),
        consumes=("api_contract", "integrated_validation", "operator_guide"),
        produces=("release_readiness_record",),
        impact=ImpactClass.HIGH,
        entry_gates=(release_evidence,),
        exit_gates=(outputs_present,),
    )
    return Plan(
        revision=1,
        context_version=1,
        tasks=(normalize, design, core, analytics, validation, documentation, release),
        created_at=created_at,
    )


def _gate_evaluator(raw_requirement: str):
    required_terms = (
        "creates",
        "redirects",
        "analytics",
        "health",
        "readiness",
        "openapi",
        "collision",
        "idempotency",
    )

    def evaluate(
        gate: Gate,
        task: Task,
        artifacts: Mapping[str, Artifact],
    ) -> tuple[bool, str]:
        if gate.id == "requirement-structure":
            missing = [term for term in required_terms if term not in raw_requirement.lower()]
            return (
                not missing,
                "all required product outcomes are explicit"
                if not missing
                else "requirement is incomplete; missing: " + ", ".join(missing),
            )
        expected = set(task.produces if gate.kind is GateKind.EXIT else task.consumes)
        missing = sorted(expected - set(artifacts))
        stale = sorted(name for name in expected if name in artifacts and artifacts[name].stale)
        passed = not missing and not stale
        details: list[str] = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if stale:
            details.append("stale: " + ", ".join(stale))
        return passed, "declared evidence is complete and fresh" if passed else "; ".join(details)

    return evaluate


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


class _CollisionGenerator:
    """Force a first-candidate collision and then provide a stable fallback."""

    def candidate(self, destination: str, collision_index: int) -> str:
        if collision_index == 0:
            return "shared00"
        return "fallback" + str(collision_index)


def _smoke_validation(workspace: Path) -> dict[str, bool]:
    """Exercise product behavior directly, without a network or cloud dependency."""
    temporary_root = workspace / "tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    checks: dict[str, bool] = {}
    # A unique scratch directory prevents an interrupted Windows run from
    # leaving a SQLite file handle that can affect the next deterministic run.
    with tempfile.TemporaryDirectory(
        prefix="s01-smoke-", dir=temporary_root, ignore_cleanup_errors=True
    ) as scratch:
        smoke_db = Path(scratch) / "smoke.db"
        collision_db = Path(scratch) / "collision.db"
        with TestClient(create_app(smoke_db)) as client:
            destination = "https://example.com/greenfield-smoke"
            created = client.post("/links", json={"destination": destination})
            checks["create"] = created.status_code == 201
            code = created.json()["code"]
            repeated = client.post("/links", json={"destination": destination})
            checks["idempotency"] = repeated.status_code == 201 and repeated.json()["code"] == code
            redirected = client.get(f"/{code}", follow_redirects=False)
            checks["redirect"] = redirected.status_code == 307 and redirected.headers["location"] == destination
            stats = client.get(f"/links/{code}/stats")
            checks["analytics"] = stats.status_code == 200 and stats.json()["click_count"] == 1
            checks["invalid_input"] = (
                client.post("/links", json={"destination": "javascript:alert(1)"}).status_code
                == 422
            )
            checks["not_found"] = client.get("/missing-code", follow_redirects=False).status_code == 404
            checks["health"] = client.get("/health").json() == {"status": "ok"}
            checks["readiness"] = client.get("/ready").json() == {"status": "ready"}
            contract = client.get("/openapi.json")
            paths = contract.json()["paths"]
            checks["openapi"] = contract.status_code == 200 and {
                "/links",
                "/{code}",
                "/links/{code}/stats",
                "/health",
                "/ready",
            }.issubset(paths)

        with TestClient(
            create_app(collision_db, code_generator=_CollisionGenerator())
        ) as collision_client:
            first = collision_client.post(
                "/links", json={"destination": "https://example.com/first"}
            )
            second = collision_client.post(
                "/links", json={"destination": "https://example.com/second"}
            )
            checks["collision"] = (
                first.status_code == second.status_code == 201
                and first.json()["code"] == "shared00"
                and second.json()["code"] == "fallback1"
            )
    return checks


def _executor(workspace: Path) -> ScriptedFailureExecutor:
    def normalize(_task: Task, _inputs: dict[str, str]) -> TaskOutput:
        return TaskOutput(
            summary="well-defined request normalized",
            artifacts={
                "normalized_requirement": _json(
                    {
                        "problem": RAW_REQUIREMENT,
                        "scope": ["create", "redirect", "analytics", "health", "readiness"],
                        "non_goals": ["UI", "custom aliases", "authentication", "rate limiting"],
                    }
                )
            },
        )

    def design(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        contract = create_app(workspace / "tmp" / "s01-contract.db").openapi()
        return TaskOutput(
            summary="FastAPI and SQLite baseline design fixed",
            artifacts={
                "service_design": _json(
                    {
                        "transport": "FastAPI",
                        "persistence": "SQLite",
                        "code_generation": "deterministic SHA-256 candidates",
                        "analytics": "aggregate count and timestamps only",
                        "requirement_hash_input": inputs["normalized_requirement"],
                    }
                ),
                "api_contract": _json(contract),
            },
        )

    def core(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        return TaskOutput(
            summary="create, idempotency, collision, and redirect implementation located",
            artifacts={
                "core_implementation": _json(
                    {
                        "modules": ["app/main.py", "app/service.py", "app/repository.py", "app/codes.py"],
                        "consumed": sorted(inputs),
                        "behaviors": ["create", "redirect", "idempotency", "bounded collision fallback"],
                    }
                )
            },
        )

    def analytics(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        return TaskOutput(
            summary="analytics, health, and readiness implementation located",
            artifacts={
                "analytics_reliability_implementation": _json(
                    {
                        "modules": ["app/main.py", "app/repository.py", "app/models.py"],
                        "consumed": sorted(inputs),
                        "behaviors": ["click count", "recent timestamps", "health", "readiness"],
                        "privacy_boundary": "no raw client identifiers",
                    }
                )
            },
        )

    def validate(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        checks = _smoke_validation(workspace)
        return TaskOutput(
            summary="unit-shaped and integrated product checks passed after transient recovery",
            artifacts={
                "integrated_validation": _json(
                    {
                        "checks": checks,
                        "source_suites": [
                            "tests/test_shortener_service.py",
                            "tests/test_shortener_api.py",
                            "tests/test_shortener_live.py",
                        ],
                        "branch_inputs": sorted(inputs),
                    }
                ),
                "smoke_result": _json(
                    {"path": "create -> redirect -> analytics", "passed": all(checks.values())}
                ),
            },
            validation_results=checks,
        )

    def document(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        return TaskOutput(
            summary="baseline documentation references published",
            artifacts={
                "operator_guide": _json(
                    {
                        "start": ".venv/Scripts/python.exe -m uvicorn app.main:app",
                        "test": ".venv/Scripts/python.exe -m pytest tests/ -q",
                        "contract": "GET /openapi.json",
                        "evidence_inputs": sorted(inputs),
                    }
                )
            },
        )

    def release(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        return TaskOutput(
            summary="human-approved release-readiness evidence assembled",
            artifacts={
                "release_readiness_record": _json(
                    {
                        "approved_by": HUMAN_RELEASE_MANAGER.id,
                        "evidence": sorted(inputs),
                        "status": "ready for baseline review",
                    }
                )
            },
        )

    inner = DeterministicExecutor(
        {
            "normalize-requirement": normalize,
            "design-baseline": design,
            "implement-core": core,
            "implement-analytics-reliability": analytics,
            "integrated-validation": validate,
            "document-baseline": document,
            "release-readiness": release,
        }
    )
    return ScriptedFailureExecutor(inner, failures={"integrated-validation": 1})


def execute(context: ScenarioContext) -> ScenarioExecution:
    clock, ids = deterministic_pair()
    requirement = ContextVersion(
        version=1,
        raw_requirement=RAW_REQUIREMENT,
        normalized_problem=(
            "Deliver the bounded FastAPI/SQLite URL-shortener baseline through a "
            "governed SDLC DAG and retain replayable evidence."
        ),
        assumptions=(
            "The deterministic credential-free executor is the reviewer path.",
            "Basic analytics retain click timestamps but no raw client identifiers.",
            "A scripted human release-manager fixture represents the attributable release decision.",
        ),
        acceptance_checks=(
            "create -> redirect -> analytics smoke path passes",
            "invalid, collision, idempotency, not-found, health, readiness, and OpenAPI checks pass",
            "implementation forks and joins before integrated validation",
            "one transient failure recovers within one retry",
            "javascript URL fixture is denied while safe work continues",
            "release-readiness requires an attributable human approval",
        ),
        created_at=clock.iso(),
    )
    plan = build_plan(created_at=clock.iso())
    engine = OrchestrationEngine(
        plan,
        _executor(context.workspace),
        clock=clock,
        id_gen=ids,
        run_id=RUN_ID,
        gate_evaluator=_gate_evaluator(requirement.raw_requirement),
        required_policies={"integrated-validation": (URL_SAFETY_POLICY,)},
    )
    engine.start(actor=AGENT)
    engine.record_context_version(requirement, actor=AGENT)
    architecture_decision = Decision(
        id=ids.next_id("decision"),
        summary="Use a deterministic FastAPI and SQLite baseline",
        rationale=(
            "It provides a production-shaped vertical slice while keeping the "
            "reviewer path local, bounded, and credential-free."
        ),
        actor=AGENT,
        created_at=clock.iso(),
        task_id="design-baseline",
        context_version=1,
    )
    engine.record_decision(architecture_decision)

    policies = PolicyEngine(clock=clock)
    engine.record_policy_decision(
        policies.evaluate_url("integrated-validation", "javascript:alert(1)"),
        actor=AGENT,
    )
    engine.record_policy_decision(
        policies.evaluate_url(
            "integrated-validation", "https://example.com/greenfield-smoke"
        ),
        actor=AGENT,
    )

    assert engine.run(actor=AGENT) is RunState.AWAITING_APPROVAL
    assert engine.task("release-readiness").state is TaskState.AWAITING_APPROVAL
    approval = Approval(
        id=ids.next_id("approval"),
        task_id="release-readiness",
        granted=True,
        actor=HUMAN_RELEASE_MANAGER,
        rationale="integrated validation, API contract, and operator evidence reviewed",
        decided_at=clock.iso(),
    )
    assert engine.decide_approval(approval) is RunState.SUCCEEDED

    return ScenarioExecution(
        engine=engine,
        limitations=(
            "The release approval is a deterministic human-attributed fixture, not an external identity-provider interaction.",
            "The smoke path uses FastAPI's in-process test client; the separate live-server suite covers process startup.",
        ),
        reviewable_outputs={
            "service": "app/main.py",
            "persistence": "app/repository.py",
            "api_tests": "tests/test_shortener_api.py",
            "live_smoke_test": "tests/test_shortener_live.py",
            "governance_tests": "tests/test_governance_negative.py",
        },
    )


SCENARIO = ScenarioSpec(
    scenario_id=SCENARIO_ID,
    title="Greenfield core service",
    execute=execute,
    aliases=("greenfield", "s01"),
)


__all__ = ["RUN_ID", "SCENARIO", "SCENARIO_ID", "build_plan", "execute"]
