"""S-02: repository-grounded brownfield reliability change.

The scenario treats the S-01 result as the established baseline.  It inspects
the repository and reproduces the former collision/idempotency defect before a
change plan may run.  The corrected behavior is then validated on the real
SQLite repository.  A later, deliberately persistent release-verification
failure exhausts its retry budget, executes a registered state-restoration
handler, and safe-stops without claiming release success.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from pathlib import Path

from app.repository import SqliteLinkRepository
from orchestrator.agents import Agent, AgentRegistry
from orchestrator.clock import deterministic_pair
from orchestrator.contracts import (
    Actor,
    ActorKind,
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
from orchestrator.executor import TaskFailure, TaskOutput
from orchestrator.policy import CHANGE_CONTROL_POLICY, PolicyEngine
from orchestrator.recovery import RecoveryController
from scenarios.approvals import ApprovalProvider
from scenarios.runner import ScenarioContext, ScenarioError, ScenarioExecution, ScenarioSpec


SCENARIO_ID = "s-02"
RUN_ID = "s-02-brownfield"
COMPENSATION_NAME = "restore-s01-release-candidate"

AGENT = Actor(kind=ActorKind.AGENT, id="agent:s02-orchestrator")
HUMAN_CHANGE_OWNER = Actor(
    kind=ActorKind.HUMAN,
    id="reviewer:s02-change-owner",
)

RAW_REQUIREMENT = (
    "Correct collision/idempotency reliability without changing the HTTP contract; "
    "perform repository impact analysis and reproduce the defect before planning, "
    "then provide gated red-to-green and regression evidence."
)


def _gate(gate_id: str, kind: GateKind, description: str) -> Gate:
    return Gate(id=gate_id, kind=kind, description=description)


def build_plan(*, created_at: str) -> Plan:
    """Build the impact -> red test -> plan -> fork/join -> safe-stop DAG."""
    outputs_present = _gate(
        "declared-outputs-present",
        GateKind.EXIT,
        "Every declared output must exist before task success.",
    )
    inputs_fresh = _gate(
        "declared-inputs-fresh",
        GateKind.ENTRY,
        "Every declared input must exist and be fresh.",
    )
    repository_present = _gate(
        "s01-baseline-and-repository-present",
        GateKind.ENTRY,
        "The successful S-01 baseline and cited repository sources must exist.",
    )
    red_before_plan = _gate(
        "impact-and-red-required-before-planning",
        GateKind.ENTRY,
        "Repository impact analysis and a failing legacy regression fixture gate planning.",
    )
    green_before_join = _gate(
        "green-proof-and-docs-required",
        GateKind.ENTRY,
        "Focused green proof and updated documentation must synchronize before validation.",
    )

    impact = Task(
        id="analyze-baseline-impact",
        name="Inspect S-01 repository impact before change planning",
        stage=Stage.REQUIREMENTS,
        capability="brownfield-analyst",
        produces=("impact_analysis",),
        entry_gates=(repository_present,),
        exit_gates=(outputs_present,),
    )
    reproduce = Task(
        id="reproduce-collision-idempotency-defect",
        name="Run red collision/idempotency regression fixture",
        stage=Stage.TESTING,
        capability="quality-engineer",
        depends_on=(impact.id,),
        consumes=("impact_analysis",),
        produces=("red_regression",),
        entry_gates=(inputs_fresh,),
        exit_gates=(outputs_present,),
    )
    plan_change = Task(
        id="plan-gated-fix",
        name="Plan the bounded reliability correction",
        stage=Stage.DESIGN,
        capability="solution-architect",
        depends_on=(reproduce.id,),
        consumes=("impact_analysis", "red_regression"),
        produces=("change_contract",),
        entry_gates=(red_before_plan,),
        exit_gates=(outputs_present,),
    )
    implement = Task(
        id="apply-collision-idempotency-fix",
        name="Apply the repository ordering correction",
        stage=Stage.IMPLEMENTATION,
        capability="backend-engineer",
        depends_on=(plan_change.id,),
        consumes=("change_contract",),
        produces=("fix_implementation",),
        impact=ImpactClass.HIGH,
        entry_gates=(inputs_fresh,),
        exit_gates=(outputs_present,),
    )
    focused = Task(
        id="validate-fixed-behavior",
        name="Run focused and regression validation",
        stage=Stage.TESTING,
        capability="quality-engineer",
        depends_on=(implement.id,),
        consumes=("fix_implementation", "change_contract"),
        produces=("green_regression", "regression_report"),
        entry_gates=(inputs_fresh,),
        exit_gates=(outputs_present,),
    )
    documentation = Task(
        id="document-reliability-change",
        name="Document the bounded behavior change",
        stage=Stage.DOCUMENTATION,
        capability="technical-writer",
        depends_on=(implement.id,),
        consumes=("fix_implementation", "change_contract"),
        produces=("change_documentation",),
        entry_gates=(inputs_fresh,),
        exit_gates=(outputs_present,),
    )
    integrated = Task(
        id="synchronize-before-after-proof",
        name="Join red, green, regression, and documentation evidence",
        stage=Stage.TESTING,
        capability="release-quality-engineer",
        depends_on=(focused.id, documentation.id),
        consumes=(
            "red_regression",
            "green_regression",
            "regression_report",
            "change_documentation",
        ),
        produces=("before_after_proof",),
        entry_gates=(green_before_join,),
        exit_gates=(outputs_present,),
    )
    release = Task(
        id="verify-release-candidate",
        name="Verify the brownfield release candidate",
        stage=Stage.RELEASE_READINESS,
        capability="release-engineer",
        depends_on=(integrated.id,),
        consumes=("before_after_proof",),
        produces=("release_verification",),
        entry_gates=(inputs_fresh,),
        exit_gates=(outputs_present,),
        retry_budget=1,
        compensation=COMPENSATION_NAME,
    )
    return Plan(
        revision=1,
        context_version=1,
        tasks=(
            impact,
            reproduce,
            plan_change,
            implement,
            focused,
            documentation,
            integrated,
            release,
        ),
        created_at=created_at,
    )


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _legacy_candidate_selection(
    *, destination: str, existing_by_destination: Mapping[str, str], occupied: set[str]
) -> str:
    """Model the former defect: allocate by code before checking idempotency."""
    del existing_by_destination
    candidates = ("shared00", "fallback1", "fallback2")
    return next(code for code in candidates if code not in occupied)


class _CollisionGenerator:
    def candidate(self, _destination: str, collision_index: int) -> str:
        return "shared00" if collision_index == 0 else f"fallback{collision_index}"


def _gate_evaluator(
    workspace: Path,
    baseline_succeeded: bool,
    observations: dict[str, bool],
):
    cited_paths = (
        workspace / "app" / "repository.py",
        workspace / "app" / "service.py",
        workspace / "app" / "main.py",
        workspace / "tests" / "test_shortener_api.py",
        workspace / "README.md",
        workspace / "docs" / "README.md",
    )

    def evaluate(
        gate: Gate,
        task: Task,
        artifacts: Mapping[str, Artifact],
    ) -> tuple[bool, str]:
        if gate.id == "s01-baseline-and-repository-present":
            missing = [
                path.relative_to(workspace).as_posix()
                for path in cited_paths
                if not path.is_file()
            ]
            passed = baseline_succeeded and not missing
            if not baseline_succeeded:
                return False, "S-01 baseline is missing or did not succeed"
            return (
                passed,
                "S-01 baseline and repository sources are present"
                if passed
                else "missing repository sources: " + ", ".join(missing),
            )
        if gate.id == "impact-and-red-required-before-planning":
            required = {"impact_analysis", "red_regression"}
            missing = sorted(required - set(artifacts))
            passed = (
                not missing
                and observations.get("impact_grounded") is True
                and observations.get("legacy_regression_red") is True
            )
            return (
                passed,
                "repository impact and red regression evidence precede change planning"
                if passed
                else "planning denied: impact analysis or red regression proof is incomplete",
            )
        if gate.id == "green-proof-and-docs-required":
            required = {"green_regression", "regression_report", "change_documentation"}
            passed = (
                not (required - set(artifacts))
                and observations.get("fixed_regression_green") is True
            )
            return (
                passed,
                "green regression proof and documentation synchronized"
                if passed
                else "join denied: green regression proof or documentation is incomplete",
            )

        expected = set(task.produces if gate.kind is GateKind.EXIT else task.consumes)
        missing = sorted(expected - set(artifacts))
        stale = sorted(name for name in expected if name in artifacts and artifacts[name].stale)
        passed = not missing and not stale
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if stale:
            details.append("stale: " + ", ".join(stale))
        return passed, "declared evidence is complete and fresh" if passed else "; ".join(details)

    return evaluate


def _executor(
    workspace: Path,
    baseline_run_id: str,
    observations: dict[str, bool],
) -> AgentRegistry:
    def analyze(_task: Task, _inputs: dict[str, str]) -> TaskOutput:
        citations = {
            "API": ["app/main.py: POST /links"],
            "domain": ["app/service.py: ShortenerService.create"],
            "persistence": ["app/repository.py: SqliteLinkRepository.create_or_get"],
            "code_generation": ["app/codes.py: CodeGenerator.candidate"],
            "tests": ["tests/test_shortener_api.py", "tests/test_shortener_service.py"],
            "documentation": ["README.md", "docs/README.md"],
        }
        flattened = [path.split(":", 1)[0] for paths in citations.values() for path in paths]
        observations["impact_grounded"] = all((workspace / path).is_file() for path in flattened)
        return TaskOutput(
            summary="repository-grounded impact analysis completed before change planning",
            artifacts={
                "impact_analysis": _json(
                    {
                        "baseline_run_id": baseline_run_id,
                        "change": "destination lookup must precede candidate allocation",
                        "citations": citations,
                        "data_flow": (
                            "POST /links -> service.create -> "
                            "repository.create_or_get -> SQLite links"
                        ),
                        "contract_effect": "none; POST /links remains idempotent with HTTP 201",
                        "migration": "none",
                        "grounded": observations["impact_grounded"],
                    }
                )
            },
            validation_results={"repository_citations_exist": observations["impact_grounded"]},
        )

    def reproduce(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        destination = "https://example.com/idempotent-after-collision"
        expected = "fallback1"
        actual = _legacy_candidate_selection(
            destination=destination,
            existing_by_destination={destination: expected},
            occupied={"shared00", "fallback1"},
        )
        observations["legacy_regression_red"] = actual != expected
        return TaskOutput(
            summary="legacy collision/idempotency regression reproduced red",
            artifacts={
                "red_regression": _json(
                    {
                        "fixture": "duplicate destination after first-candidate collision",
                        "expected_code": expected,
                        "legacy_actual_code": actual,
                        "assertion_passed": actual == expected,
                        "status": "red" if actual != expected else "unexpected-green",
                        "impact_input_present": "impact_analysis" in inputs,
                    }
                )
            },
            validation_results={"legacy_fixture_reproduces_defect": actual != expected},
        )

    def plan_change(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        return TaskOutput(
            summary="non-breaking persistence correction planned from impact and red proof",
            artifacts={
                "change_contract": _json(
                    {
                        "classification": ImpactClass.HIGH.value,
                        "breaking": False,
                        "implementation": "lookup destination before bounded code allocation",
                        "acceptance": (
                            "repeat creates return the original code even after collisions"
                        ),
                        "consumed": sorted(inputs),
                    }
                )
            },
        )

    def implement(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        source = (workspace / "app" / "repository.py").read_text(encoding="utf-8")
        destination_lookup = source.find("WHERE destination = ?")
        collision_loop = source.find("for collision_index in range")
        ordering_correct = 0 <= destination_lookup < collision_loop
        return TaskOutput(
            summary="destination-first idempotency ordering verified in repository implementation",
            artifacts={
                "fix_implementation": _json(
                    {
                        "path": "app/repository.py",
                        "behavior": "destination lookup precedes candidate collision loop",
                        "ordering_verified": ordering_correct,
                        "change_contract_present": "change_contract" in inputs,
                    }
                )
            },
            validation_results={"destination_lookup_precedes_allocation": ordering_correct},
        )

    def validate(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        scratch_root = workspace / "tmp"
        scratch_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="s02-regression-", dir=scratch_root, ignore_cleanup_errors=True
        ) as scratch:
            repository = SqliteLinkRepository(Path(scratch) / "links.db")
            repository.initialize()
            generator = _CollisionGenerator()
            first = repository.create_or_get(
                "https://example.com/occupies-first",
                "2026-01-01T00:00:00+00:00",
                lambda index: generator.candidate("first", index),
            )
            created = repository.create_or_get(
                "https://example.com/idempotent-after-collision",
                "2026-01-01T00:00:01+00:00",
                lambda index: generator.candidate("target", index),
            )
            repeated = repository.create_or_get(
                "https://example.com/idempotent-after-collision",
                "2026-01-01T00:00:02+00:00",
                lambda index: generator.candidate("target", index),
            )
        checks = {
            "collision_forced": first.code == "shared00" and created.code == "fallback1",
            "idempotent_code": repeated.code == created.code,
            "idempotent_timestamp": repeated.created_at == created.created_at,
            "branch_inputs_complete": set(inputs) == {"change_contract", "fix_implementation"},
        }
        observations["fixed_regression_green"] = all(checks.values())
        return TaskOutput(
            summary="focused collision/idempotency and regression checks passed",
            artifacts={
                "green_regression": _json(
                    {
                        "fixture": "duplicate destination after first-candidate collision",
                        "created_code": created.code,
                        "repeated_code": repeated.code,
                        "status": "green" if all(checks.values()) else "red",
                    }
                ),
                "regression_report": _json(
                    {
                        "checks": checks,
                        "unchanged_contract": (
                            "POST /links returns stable LinkResponse and Location"
                        ),
                        "source_suites": [
                            "tests/test_shortener_api.py",
                            "tests/test_shortener_service.py",
                            "tests/test_shortener_live.py",
                        ],
                    }
                ),
            },
            validation_results=checks,
        )

    def document(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        return TaskOutput(
            summary="reliability behavior and validation references synchronized",
            artifacts={
                "change_documentation": _json(
                    {
                        "behavior": "create is idempotent across deterministic collisions",
                        "contract_change": False,
                        "migration_required": False,
                        "references": ["README.md", "docs/README.md"],
                        "consumed": sorted(inputs),
                    }
                )
            },
        )

    def integrate(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        red = json.loads(inputs["red_regression"])
        green = json.loads(inputs["green_regression"])
        checks = {
            "before_is_red": red["status"] == "red" and red["assertion_passed"] is False,
            "after_is_green": green["status"] == "green",
            "fork_outputs_joined": {
                "regression_report",
                "change_documentation",
            }.issubset(inputs),
        }
        return TaskOutput(
            summary="red-to-green branches synchronized before release verification",
            artifacts={
                "before_after_proof": _json(
                    {
                        "checks": checks,
                        "before": red,
                        "after": green,
                        "joined_inputs": sorted(inputs),
                    }
                )
            },
            validation_results=checks,
        )

    def verify_release_candidate(_task: Task, _inputs: dict[str, str]) -> TaskOutput:
        raise TaskFailure(
            "injected persistent release-candidate verification failure",
            transient=True,
        )

    agents = AgentRegistry()
    for agent in (
        Agent(
            "agent:brownfield-analyst",
            "brownfield-analyst",
            {"analyze-baseline-impact": analyze},
        ),
        Agent(
            "agent:quality-engineer",
            "quality-engineer",
            {
                "reproduce-collision-idempotency-defect": reproduce,
                "validate-fixed-behavior": validate,
            },
        ),
        Agent("agent:solution-architect", "solution-architect", {"plan-gated-fix": plan_change}),
        Agent(
            "agent:backend-engineer",
            "backend-engineer",
            {"apply-collision-idempotency-fix": implement},
        ),
        Agent(
            "agent:technical-writer",
            "technical-writer",
            {"document-reliability-change": document},
        ),
        Agent(
            "agent:release-quality-engineer",
            "release-quality-engineer",
            {"synchronize-before-after-proof": integrate},
        ),
        Agent(
            "agent:release-engineer",
            "release-engineer",
            {"verify-release-candidate": verify_release_candidate},
        ),
    ):
        agents.register(agent)
    return agents


def execute(
    context: ScenarioContext,
    approval_provider: ApprovalProvider | None = None,
) -> ScenarioExecution:
    approval_provider = (
        approval_provider
        if approval_provider is not None
        else context.approval_provider
    )
    baseline = context.prior_results.get("s-01")
    if baseline is None or baseline.state is not RunState.SUCCEEDED:
        raise ScenarioError("S-02 requires a successful S-01 baseline result")

    clock, ids = deterministic_pair()
    requirement = ContextVersion(
        version=1,
        raw_requirement=RAW_REQUIREMENT,
        normalized_problem=(
            "Preserve destination idempotency across deterministic short-code collisions, "
            "with impact-first planning and governed recovery evidence."
        ),
        assumptions=(
            f"S-01 baseline run '{baseline.run_id}' is the brownfield starting point.",
            "The legacy regression fixture represents code-first allocation before the correction.",
            "Release-candidate state restoration is bounded to an in-memory scenario pointer.",
        ),
        acceptance_checks=(
            "repository impact analysis and a red fixture precede change planning",
            "the fix requires change-control evaluation and human approval",
            "focused validation proves red-to-green collision/idempotency behavior",
            "validation and documentation fork then synchronize",
            "persistent release verification exhausts one retry",
            "registered compensation restores the S-01 candidate and the run safe-stops",
        ),
        created_at=clock.iso(),
    )
    observations: dict[str, bool] = {}
    release_state = {
        "active_candidate": "s02-brownfield-candidate",
        "restored": False,
    }

    recovery = RecoveryController()

    def restore_baseline(task: Task, failure_reason: str) -> tuple[bool, str]:
        if task.id != "verify-release-candidate":
            return False, "compensation rejected: unexpected task"
        if not failure_reason:
            return False, "compensation rejected: failure reason is missing"
        prior = release_state["active_candidate"]
        release_state["active_candidate"] = baseline.run_id
        release_state["restored"] = True
        return (
            True,
            f"release candidate pointer restored from {prior} to {baseline.run_id}",
        )

    # Register a real handler: it restores scenario-owned release-candidate
    # state and reports the exact transition.  RecoveryController invokes it;
    # the engine records the result and owns the terminal state decision.
    recovery.register(COMPENSATION_NAME, restore_baseline)

    plan = build_plan(created_at=clock.iso())
    agents = _executor(context.workspace, baseline.run_id, observations)
    engine = OrchestrationEngine(
        plan,
        agents,
        clock=clock,
        id_gen=ids,
        run_id=RUN_ID,
        gate_evaluator=_gate_evaluator(context.workspace, True, observations),
        recovery_controller=recovery,
        required_policies={"apply-collision-idempotency-fix": (CHANGE_CONTROL_POLICY,)},
    )
    engine.start(actor=AGENT)
    engine.record_context_version(requirement, actor=AGENT)
    engine.record_decision(
        Decision(
            id=ids.next_id("decision"),
            summary="Use destination-first lookup as the bounded reliability correction",
            rationale=(
                "Repository inspection shows the idempotency boundary belongs in "
                "SqliteLinkRepository.create_or_get before candidate allocation."
            ),
            actor=agents.actor_for(engine.task("plan-gated-fix")),
            created_at=clock.iso(),
            task_id="plan-gated-fix",
            context_version=1,
        )
    )

    policies = PolicyEngine(clock=clock)
    engine.record_policy_decision(
        policies.evaluate_change(
            "apply-collision-idempotency-fix",
            is_breaking=False,
            declared_impact=ImpactClass.HIGH,
        ),
        actor=agents.actor_for(engine.task("apply-collision-idempotency-fix")),
    )

    assert engine.run(actor=AGENT) is RunState.AWAITING_APPROVAL
    assert engine.task("apply-collision-idempotency-fix").state is TaskState.AWAITING_APPROVAL
    approval = approval_provider.decide(
        task_id="apply-collision-idempotency-fix",
        impact=engine.task("apply-collision-idempotency-fix").impact.value,
        summary=engine.task("apply-collision-idempotency-fix").name,
        default_actor=HUMAN_CHANGE_OWNER,
        default_rationale="impact analysis, red regression, and non-breaking change contract reviewed",
        approval_id=ids.next_id("approval"),
        decided_at=clock.iso(),
    )
    terminal_state = engine.decide_approval(approval)
    if not approval.granted:
        return ScenarioExecution(
            engine=engine,
            terminal_reason=(
                f"Human approval denied for '{approval.task_id}' by "
                f"'{approval.actor.id}': {approval.rationale}"
            ),
        )
    assert terminal_state is RunState.SAFE_STOPPED
    assert release_state == {"active_candidate": baseline.run_id, "restored": True}
    assert engine.task("verify-release-candidate").state is TaskState.COMPENSATED
    assert engine.metrics.retry_count == 1
    assert engine.metrics.rollback_count == 1
    assert engine.metrics.mttr_seconds is not None

    return ScenarioExecution(
        engine=engine,
        terminal_reason=(
            "Persistent release verification exhausted its retry budget; the registered "
            "handler restored the S-01 release candidate and the run safe-stopped."
        ),
        limitations=(
            (
                "Compensation restores the scenario-owned release-candidate pointer; "
                "it does not perform source-control, database, or deployment rollback."
            ),
            (
                "The red fixture executes the former code-first allocation behavior "
                "while the green fixture exercises the current SQLite repository."
            ),
        ),
        reviewable_outputs={
            "repository_fix": "app/repository.py",
            "service_boundary": "app/service.py",
            "api_boundary": "app/main.py",
            "api_regression_tests": "tests/test_shortener_api.py",
            "service_regression_tests": "tests/test_shortener_service.py",
        },
    )


SCENARIO = ScenarioSpec(
    scenario_id=SCENARIO_ID,
    title="Brownfield reliability change",
    execute=execute,
    dependencies=("s-01",),
    aliases=("brownfield", "s02"),
    acceptable_states=frozenset({RunState.SAFE_STOPPED}),
)


__all__ = [
    "COMPENSATION_NAME",
    "RUN_ID",
    "SCENARIO",
    "SCENARIO_ID",
    "build_plan",
    "execute",
]
