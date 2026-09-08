"""S-03: ambiguous analytics request resolved through a governed re-plan.

The first context deliberately leaves analytics precision, privacy, retention,
success thresholds, and scope unresolved.  The engine pauses before
normalization for human approval, executes parallel design/test-documentation
planning, then stops at a clarification checkpoint.  A human privacy
clarification creates context v2 and selectively invalidates only the analytics
lineage.  Unaffected baseline evidence and the full v1 history are retained.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from collections.abc import Mapping
from pathlib import Path

from app.codes import Sha256CodeGenerator
from app.repository import SqliteLinkRepository
from app.service import ShortenerService
from orchestrator.clock import FixedClock, deterministic_pair
from orchestrator.contracts import (
    Actor,
    ActorKind,
    Ambiguity,
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
from orchestrator.executor import DeterministicExecutor, TaskOutput
from orchestrator.policy import (
    CHANGE_CONTROL_POLICY,
    EVIDENCE_RETENTION_POLICY,
    PRIVACY_POLICY,
    PolicyEngine,
)
from scenarios.runner import ScenarioContext, ScenarioError, ScenarioExecution, ScenarioSpec


SCENARIO_ID = "s-03"
RUN_ID = "s-03-ambiguous"
RAW_REQUIREMENT = "Improve link analytics."

AGENT = Actor(kind=ActorKind.AGENT, id="agent:s03-orchestrator")
HUMAN_PRODUCT_OWNER = Actor(
    kind=ActorKind.HUMAN,
    id="reviewer:s03-product-owner",
)
HUMAN_PRIVACY_OWNER = Actor(
    kind=ActorKind.HUMAN,
    id="reviewer:s03-privacy-owner",
)
HUMAN_QUALITY_OWNER = Actor(
    kind=ActorKind.HUMAN,
    id="reviewer:s03-quality-owner",
)

AMBIGUITIES = (
    Ambiguity(
        id="analytics-dimension",
        question="Which analytics dimension should be added, and at what precision?",
        proposed_assumption="Add a UTC calendar-day aggregate only.",
        consequence_if_wrong="The result may be less granular than stakeholders expect.",
    ),
    Ambiguity(
        id="privacy-retention",
        question="May raw client identifiers be stored, and for how long?",
        proposed_assumption="Store no client identifiers and derive only bounded aggregates.",
        consequence_if_wrong="A more permissive choice would create privacy and retention risk.",
    ),
    Ambiguity(
        id="acceptance-threshold",
        question="What observable result proves the analytics improvement?",
        proposed_assumption="Counts must group deterministically across two UTC days.",
        consequence_if_wrong="The change could ship without a measurable quality threshold.",
    ),
    Ambiguity(
        id="scope-boundary",
        question="Does improve include geolocation, fingerprinting, or a new platform?",
        proposed_assumption="Keep the existing API and SQLite service; add no tracking platform.",
        consequence_if_wrong="Unbounded scope would invalidate delivery and privacy estimates.",
    ),
)


def _gate(gate_id: str, kind: GateKind, description: str) -> Gate:
    return Gate(id=gate_id, kind=kind, description=description)


def build_plan(*, created_at: str, context_version: int = 1) -> Plan:
    """Build v1 or the revised v2 graph while keeping stable task identities."""
    if context_version not in (1, 2):
        raise ValueError("S-03 supports context versions 1 and 2")

    outputs = _gate(
        "declared-outputs-present",
        GateKind.EXIT,
        "Every declared output exists before task success.",
    )
    inputs = _gate(
        "declared-inputs-fresh",
        GateKind.ENTRY,
        "Every declared input exists, is fresh, and comes from a successful producer.",
    )
    ambiguity_gate = _gate(
        "ambiguities-and-assumptions-exposed",
        GateKind.EXIT,
        "Missing dimensions, assumptions, and consequences are explicit.",
    )
    normalize_gate = _gate(
        "assumption-set-ready-for-human-decision",
        GateKind.ENTRY,
        "Normalization cannot execute before the bounded assumption set is reviewable.",
    )
    clarification_gate = _gate(
        "human-privacy-clarification-present",
        GateKind.ENTRY,
        "The v2 normalization requires an attributable human clarification.",
    )
    join_gate = _gate(
        "parallel-plans-complete-and-fresh",
        GateKind.ENTRY,
        "Test and documentation planning must both be complete and fresh.",
    )
    final_gate = _gate(
        "v2-quality-evidence-complete",
        GateKind.ENTRY,
        "Final approval requires v2 validation and contract evidence.",
    )

    surface = Task(
        id="surface-analytics-ambiguities",
        name="Classify analytics ambiguities and propose bounded assumptions",
        stage=Stage.REQUIREMENTS,
        capability="business-analyst",
        produces=("ambiguity_report", "assumption_proposal"),
        exit_gates=(ambiguity_gate,),
    )
    normalize_v1 = Task(
        id="normalize-requirement-v1",
        name="Human-approved normalization of the ambiguous request",
        stage=Stage.REQUIREMENTS,
        capability="product-owner",
        depends_on=(surface.id,),
        consumes=("ambiguity_report", "assumption_proposal"),
        produces=("normalized_requirement_v1",),
        impact=ImpactClass.HIGH,
        entry_gates=(normalize_gate,),
        exit_gates=(outputs,),
    )
    preserve = Task(
        id="preserve-core-scope",
        name="Confirm unchanged URL-shortener core scope",
        stage=Stage.DESIGN,
        capability="brownfield-analyst",
        depends_on=(normalize_v1.id,),
        consumes=("normalized_requirement_v1",),
        produces=("core_scope_assurance",),
        entry_gates=(inputs,),
        exit_gates=(outputs,),
    )

    tasks: list[Task] = [surface, normalize_v1, preserve]
    normalization_id = normalize_v1.id
    normalized_artifact = "normalized_requirement_v1"
    if context_version == 2:
        normalize_v2 = Task(
            id="normalize-requirement-v2",
            name="Normalize the human privacy clarification as requirement v2",
            stage=Stage.REQUIREMENTS,
            capability="privacy-analyst",
            depends_on=(surface.id,),
            consumes=("ambiguity_report", "assumption_proposal"),
            produces=("normalized_requirement_v2",),
            entry_gates=(clarification_gate,),
            exit_gates=(outputs,),
        )
        tasks.append(normalize_v2)
        normalization_id = normalize_v2.id
        normalized_artifact = "normalized_requirement_v2"

    design = Task(
        id="design-analytics-change",
        name=(
            "Design tentative analytics improvement"
            if context_version == 1
            else "Design coarse, non-identifying daily analytics"
        ),
        stage=Stage.DESIGN,
        capability="solution-architect",
        depends_on=(normalization_id,),
        consumes=(normalized_artifact,),
        produces=("analytics_design",),
        entry_gates=(inputs,),
        exit_gates=(outputs,),
    )
    tests = Task(
        id="plan-analytics-tests",
        name="Plan analytics and privacy validation",
        stage=Stage.TESTING,
        capability="quality-engineer",
        depends_on=(design.id,),
        consumes=("analytics_design",),
        produces=("analytics_test_plan",),
        entry_gates=(inputs,),
        exit_gates=(outputs,),
    )
    docs = Task(
        id="plan-analytics-documentation",
        name="Plan contract and limitation documentation",
        stage=Stage.DOCUMENTATION,
        capability="technical-writer",
        depends_on=(design.id,),
        consumes=("analytics_design",),
        produces=("analytics_documentation_plan",),
        entry_gates=(inputs,),
        exit_gates=(outputs,),
    )
    join = Task(
        id="join-analytics-plans",
        name="Synchronize analytics test and documentation plans",
        stage=Stage.DESIGN,
        capability="delivery-lead",
        depends_on=(tests.id, docs.id),
        consumes=("analytics_test_plan", "analytics_documentation_plan"),
        produces=("joined_analytics_plan",),
        entry_gates=(join_gate,),
        exit_gates=(outputs,),
    )
    checkpoint = Task(
        id="clarify-analytics-privacy",
        name="Human clarification of analytics privacy and scope",
        stage=Stage.RELEASE_READINESS,
        capability="privacy-owner",
        depends_on=(join.id,),
        consumes=("joined_analytics_plan",),
        produces=("clarification_record",),
        impact=ImpactClass.HIGH,
        entry_gates=(inputs,),
        exit_gates=(outputs,),
    )
    implement = Task(
        id="implement-coarse-analytics",
        name="Implement UTC daily click aggregates without client identity",
        stage=Stage.IMPLEMENTATION,
        capability="backend-engineer",
        depends_on=(checkpoint.id, preserve.id),
        consumes=("clarification_record", "joined_analytics_plan", "core_scope_assurance"),
        produces=("analytics_implementation",),
        impact=ImpactClass.HIGH,
        entry_gates=(inputs,),
        exit_gates=(outputs,),
    )
    validate = Task(
        id="validate-coarse-analytics",
        name="Validate aggregation precision and prohibited-data absence",
        stage=Stage.TESTING,
        capability="quality-engineer",
        depends_on=(implement.id,),
        consumes=("analytics_implementation",),
        produces=("analytics_validation",),
        entry_gates=(inputs,),
        exit_gates=(outputs,),
    )
    document = Task(
        id="document-coarse-analytics",
        name="Document the v2 analytics contract and precision limits",
        stage=Stage.DOCUMENTATION,
        capability="technical-writer",
        depends_on=(implement.id,),
        consumes=("analytics_implementation",),
        produces=("analytics_contract_documentation",),
        entry_gates=(inputs,),
        exit_gates=(outputs,),
    )
    quality = Task(
        id="final-quality-approval",
        name="Human final quality approval against requirement v2",
        stage=Stage.RELEASE_READINESS,
        capability="quality-owner",
        depends_on=(validate.id, document.id),
        consumes=("analytics_validation", "analytics_contract_documentation"),
        produces=("final_quality_record",),
        impact=ImpactClass.HIGH,
        entry_gates=(final_gate,),
        exit_gates=(outputs,),
    )
    tasks.extend((design, tests, docs, join, checkpoint, implement, validate, document, quality))
    return Plan(
        revision=context_version,
        context_version=context_version,
        tasks=tuple(tasks),
        created_at=created_at,
        supersedes=1 if context_version == 2 else None,
        change_reason=(
            "Human privacy clarification disallows raw client identifiers and bounds precision."
            if context_version == 2
            else None
        ),
    )


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _gate_evaluator(observations: dict[str, bool]):
    def evaluate(
        gate: Gate,
        task: Task,
        artifacts: Mapping[str, Artifact],
    ) -> tuple[bool, str]:
        if gate.id == "ambiguities-and-assumptions-exposed":
            passed = len(AMBIGUITIES) == 4 and all(
                item.question and item.proposed_assumption and item.consequence_if_wrong
                for item in AMBIGUITIES
            )
            return passed, (
                "dimensions, privacy, thresholds, and scope ambiguities are explicit"
                if passed
                else "ambiguity classification is incomplete"
            )
        if gate.id == "assumption-set-ready-for-human-decision":
            passed = {"ambiguity_report", "assumption_proposal"}.issubset(artifacts)
            return passed, (
                "bounded assumptions are reviewable; execution still requires human approval"
                if passed
                else "normalization denied because assumption evidence is missing"
            )
        if gate.id == "human-privacy-clarification-present":
            passed = observations.get("human_clarification_recorded") is True
            return passed, (
                "attributable human privacy clarification is recorded"
                if passed
                else "v2 normalization denied without human clarification"
            )
        if gate.id == "parallel-plans-complete-and-fresh":
            required = {"analytics_test_plan", "analytics_documentation_plan"}
            passed = required.issubset(artifacts) and all(
                not artifacts[name].stale for name in required if name in artifacts
            )
            return passed, (
                "parallel test and documentation plans joined with fresh evidence"
                if passed
                else "join denied because a branch output is missing or stale"
            )
        if gate.id == "v2-quality-evidence-complete":
            required = {"analytics_validation", "analytics_contract_documentation"}
            passed = (
                required.issubset(artifacts)
                and observations.get("privacy_validation_passed") is True
                and observations.get("active_context_v2") is True
            )
            return passed, (
                "complete validation and contract evidence reference requirement v2"
                if passed
                else "final quality denied without complete v2 evidence"
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


def _executor(workspace: Path, phase: dict[str, int], observations: dict[str, bool]):
    def surface(_task: Task, _inputs: dict[str, str]) -> TaskOutput:
        return TaskOutput(
            summary="four material ambiguities surfaced with bounded assumptions",
            artifacts={
                "ambiguity_report": _json(
                    {"raw_requirement": RAW_REQUIREMENT, "ambiguities": [a.model_dump() for a in AMBIGUITIES]}
                ),
                "assumption_proposal": _json(
                    {
                        "status": "proposed-not-approved",
                        "assumptions": [a.proposed_assumption for a in AMBIGUITIES],
                        "requires_human_decision": True,
                    }
                ),
            },
        )

    def normalize_v1(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        return TaskOutput(
            summary="human-approved bounded assumption set normalized as v1",
            artifacts={
                "normalized_requirement_v1": _json(
                    {
                        "context_version": 1,
                        "problem": "Add one bounded aggregate to existing link analytics.",
                        "assumptions_approved": True,
                        "inputs": sorted(inputs),
                    }
                )
            },
        )

    def preserve(_task: Task, _inputs: dict[str, str]) -> TaskOutput:
        paths = ["app/main.py", "app/service.py", "app/repository.py"]
        present = all((workspace / path).is_file() for path in paths)
        return TaskOutput(
            summary="unchanged create, redirect, persistence, and health scope retained",
            artifacts={
                "core_scope_assurance": _json(
                    {
                        "unaffected": True,
                        "paths": paths,
                        "all_present": present,
                        "excluded": ["geolocation", "fingerprinting", "tracking platform"],
                    }
                )
            },
            validation_results={"baseline_paths_present": present},
        )

    def normalize_v2(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        return TaskOutput(
            summary="privacy clarification normalized as requirement v2",
            artifacts={
                "normalized_requirement_v2": _json(
                    {
                        "context_version": 2,
                        "supersedes": 1,
                        "retained_fields": ["utc_day", "click_count"],
                        "raw_client_identifiers_retained": False,
                        "precision": "UTC calendar day",
                        "inputs": sorted(inputs),
                    }
                )
            },
        )

    def design(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        version = phase["context_version"]
        return TaskOutput(
            summary=f"analytics design produced for context v{version}",
            artifacts={
                "analytics_design": _json(
                    {
                        "context_version": version,
                        "dimension": "unresolved client dimension" if version == 1 else "utc_day",
                        "precision": "unresolved" if version == 1 else "calendar day",
                        "raw_client_identifiers_retained": version == 1,
                        "consumed": sorted(inputs),
                    }
                )
            },
        )

    def plan_tests(_task: Task, _inputs: dict[str, str]) -> TaskOutput:
        return TaskOutput(
            summary=f"analytics test plan produced for context v{phase['context_version']}",
            artifacts={
                "analytics_test_plan": _json(
                    {
                        "context_version": phase["context_version"],
                        "checks": ["two-day grouping", "total consistency", "no client-id columns"],
                    }
                )
            },
        )

    def plan_docs(_task: Task, _inputs: dict[str, str]) -> TaskOutput:
        return TaskOutput(
            summary=f"analytics documentation plan produced for context v{phase['context_version']}",
            artifacts={
                "analytics_documentation_plan": _json(
                    {
                        "context_version": phase["context_version"],
                        "must_state": ["UTC-day precision", "no client identity", "bounded response"],
                    }
                )
            },
        )

    def join(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        return TaskOutput(
            summary=f"parallel analytics plans synchronized for context v{phase['context_version']}",
            artifacts={
                "joined_analytics_plan": _json(
                    {"context_version": phase["context_version"], "joined": sorted(inputs)}
                )
            },
        )

    def clarify(_task: Task, _inputs: dict[str, str]) -> TaskOutput:
        return TaskOutput(
            summary=f"human analytics/privacy checkpoint completed for context v{phase['context_version']}",
            artifacts={
                "clarification_record": _json(
                    {
                        "context_version": phase["context_version"],
                        "human_owned": True,
                        "raw_client_identifiers_retained": False,
                        "approved_dimension": "utc_day",
                    }
                )
            },
        )

    def implement(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        repository_source = (workspace / "app" / "repository.py").read_text(encoding="utf-8")
        model_source = (workspace / "app" / "models.py").read_text(encoding="utf-8")
        checks = {
            "daily_query_present": "GROUP BY substr(clicked_at, 1, 10)" in repository_source,
            "daily_contract_present": "DailyClickCountResponse" in model_source,
            "v2_inputs_complete": set(inputs) == {
                "clarification_record",
                "joined_analytics_plan",
                "core_scope_assurance",
            },
        }
        return TaskOutput(
            summary="coarse UTC-day analytics implementation verified",
            artifacts={
                "analytics_implementation": _json(
                    {
                        "context_version": 2,
                        "paths": ["app/repository.py", "app/models.py", "app/main.py"],
                        "checks": checks,
                        "raw_client_identifiers_added": False,
                    }
                )
            },
            validation_results=checks,
        )

    def validate(_task: Task, _inputs: dict[str, str]) -> TaskOutput:
        scratch_root = workspace / "tmp"
        scratch_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="s03-analytics-", dir=scratch_root, ignore_cleanup_errors=True
        ) as scratch:
            database_path = Path(scratch) / "links.db"
            repository = SqliteLinkRepository(database_path)
            repository.initialize()
            service = ShortenerService(repository, Sha256CodeGenerator(), FixedClock())
            link = service.create("https://example.com/privacy-analytics")
            repository.record_click(link.code, "2026-01-01T23:59:59+00:00")
            repository.record_click(link.code, "2026-01-02T00:00:00+00:00")
            repository.record_click(link.code, "2026-01-02T12:00:00+00:00")
            stats = service.stats(link.code)
            with sqlite3.connect(database_path) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(clicks)")}

        daily = [] if stats is None else [(item.day, item.click_count) for item in stats.daily_clicks]
        checks = {
            "daily_precision": daily == [("2026-01-02", 2), ("2026-01-01", 1)],
            "total_consistent": stats is not None and stats.click_count == 3,
            "no_raw_client_identifier_columns": columns == {"id", "code", "clicked_at"},
        }
        observations["privacy_validation_passed"] = all(checks.values())
        return TaskOutput(
            summary="v2 analytics and prohibited-data checks passed",
            artifacts={
                "analytics_validation": _json(
                    {"context_version": 2, "daily_clicks": daily, "columns": sorted(columns), "checks": checks}
                )
            },
            validation_results=checks,
        )

    def document(_task: Task, _inputs: dict[str, str]) -> TaskOutput:
        return TaskOutput(
            summary="v2 API contract and analytics limitations synchronized",
            artifacts={
                "analytics_contract_documentation": _json(
                    {
                        "context_version": 2,
                        "response_field": "daily_clicks[{day, click_count}]",
                        "precision_limit": "UTC calendar day; no sub-day dimension promised",
                        "privacy_limit": "no IP, user-agent, cookie, session, device, or client identifier retained",
                        "scope_limit": "no geolocation, fingerprinting, or analytics platform",
                    }
                )
            },
        )

    def quality(_task: Task, inputs: dict[str, str]) -> TaskOutput:
        return TaskOutput(
            summary="human quality approval evidence bound to requirement v2",
            artifacts={
                "final_quality_record": _json(
                    {
                        "context_version": 2,
                        "validation_complete": "analytics_validation" in inputs,
                        "contract_complete": "analytics_contract_documentation" in inputs,
                        "approved_by": HUMAN_QUALITY_OWNER.id,
                    }
                )
            },
        )

    return DeterministicExecutor(
        {
            "surface-analytics-ambiguities": surface,
            "normalize-requirement-v1": normalize_v1,
            "preserve-core-scope": preserve,
            "normalize-requirement-v2": normalize_v2,
            "design-analytics-change": design,
            "plan-analytics-tests": plan_tests,
            "plan-analytics-documentation": plan_docs,
            "join-analytics-plans": join,
            "clarify-analytics-privacy": clarify,
            "implement-coarse-analytics": implement,
            "validate-coarse-analytics": validate,
            "document-coarse-analytics": document,
            "final-quality-approval": quality,
        }
    )


def _approval(ids, clock, task_id: str, actor: Actor, rationale: str) -> Approval:
    return Approval(
        id=ids.next_id("approval"),
        task_id=task_id,
        granted=True,
        actor=actor,
        rationale=rationale,
        decided_at=clock.iso(),
    )


def execute(context: ScenarioContext) -> ScenarioExecution:
    prior = context.prior_results.get("s-02")
    if prior is None or prior.state is not RunState.SAFE_STOPPED:
        raise ScenarioError("S-03 requires the preserved S-02 safe-stopped result")

    clock, ids = deterministic_pair()
    phase = {"context_version": 1}
    observations: dict[str, bool] = {}
    plan_v1 = build_plan(created_at=clock.iso(), context_version=1)
    engine = OrchestrationEngine(
        plan_v1,
        _executor(context.workspace, phase, observations),
        clock=clock,
        id_gen=ids,
        run_id=RUN_ID,
        gate_evaluator=_gate_evaluator(observations),
        required_policies={
            "implement-coarse-analytics": (PRIVACY_POLICY, CHANGE_CONTROL_POLICY),
            "final-quality-approval": (
                PRIVACY_POLICY,
                CHANGE_CONTROL_POLICY,
                EVIDENCE_RETENTION_POLICY,
            ),
        },
    )

    context_v1 = ContextVersion(
        version=1,
        raw_requirement=RAW_REQUIREMENT,
        normalized_problem="Add one bounded analytics dimension after explicit human clarification.",
        ambiguities=AMBIGUITIES,
        assumptions=tuple(item.proposed_assumption for item in AMBIGUITIES),
        acceptance_checks=(
            "assumptions require human approval before normalization executes",
            "test and documentation planning fork and join",
            "material privacy clarification creates requirement v2",
            "only affected analytics descendants are invalidated",
            "stale v1 artifacts remain in history and cannot be consumed",
            "v2 privacy, change-control, validation, and quality gates pass",
        ),
        created_at=clock.iso(),
    )
    engine.start(actor=AGENT)
    engine.record_context_version(context_v1, actor=AGENT)
    engine.record_decision(
        Decision(
            id=ids.next_id("decision"),
            summary="Propose a minimal analytics assumption set; do not silently normalize",
            rationale="The request omits dimension, privacy, retention, threshold, and scope decisions.",
            actor=AGENT,
            created_at=clock.iso(),
            task_id="surface-analytics-ambiguities",
            context_version=1,
        )
    )

    # The tentative v1 design would retain a raw identifier; record the denial
    # while permitted ambiguity and scope work continues to the human checkpoint.
    policies = PolicyEngine(clock=clock)
    engine.record_policy_decision(
        policies.evaluate_privacy(
            "implement-coarse-analytics",
            ("client_id",),
            raw_client_identifiers_retained=True,
        ),
        actor=AGENT,
    )

    assert engine.run(actor=AGENT) is RunState.AWAITING_APPROVAL
    assert engine.task("normalize-requirement-v1").state is TaskState.AWAITING_APPROVAL
    assert not any(a.name == "normalized_requirement_v1" for a in engine.artifacts)
    assert engine.decide_approval(
        _approval(
            ids,
            clock,
            "normalize-requirement-v1",
            HUMAN_PRODUCT_OWNER,
            "bounded assumptions are explicit and approved for v1 planning",
        )
    ) is RunState.AWAITING_APPROVAL
    assert engine.task("clarify-analytics-privacy").state is TaskState.AWAITING_APPROVAL

    # Complete just the clarification task, leaving a deterministic RUNNING
    # boundary before any downstream implementation can be scheduled.
    assert engine.decide_approval(
        _approval(
            ids,
            clock,
            "clarify-analytics-privacy",
            HUMAN_PRIVACY_OWNER,
            "disallow raw client identifiers and require UTC-day aggregates only",
        ),
        resume=False,
    ) is RunState.RUNNING
    observations["human_clarification_recorded"] = True

    context_v2 = ContextVersion(
        version=2,
        raw_requirement=RAW_REQUIREMENT,
        normalized_problem=(
            "Expose bounded UTC daily click counts while retaining no raw client identifiers."
        ),
        assumptions=(
            "UTC calendar day is the only new precision dimension.",
            "No IP address, user-agent, cookie, session, device, or client id is retained.",
            "The existing API, SQLite persistence, and core link behavior remain in scope.",
        ),
        acceptance_checks=(
            "daily counts group correctly across two UTC dates",
            "aggregate totals remain consistent",
            "the click schema contains no raw client identifier column",
            "v1 context, plan, events, and artifacts remain reviewable",
            "unaffected core-scope evidence remains fresh",
            "final human quality approval references context v2",
        ),
        supersedes=1,
        change_reason=(
            "Privacy owner clarified that raw client identifiers are prohibited and "
            "analytics precision is limited to UTC calendar-day counts."
        ),
        created_at=clock.iso(),
    )
    phase["context_version"] = 2
    replacement = build_plan(created_at=clock.iso(), context_version=2)
    engine.replan(
        context_v2,
        changed_task_ids=("design-analytics-change",),
        replacement_tasks=replacement.tasks,
        actor=HUMAN_PRIVACY_OWNER,
    )
    observations["active_context_v2"] = True
    engine.record_decision(
        Decision(
            id=ids.next_id("decision"),
            summary="Adopt UTC-day aggregates and prohibit raw client identity in requirement v2",
            rationale=(
                "The privacy clarification is material to analytics design, tests, documentation, "
                "implementation, and quality approval, but not to the retained core service scope."
            ),
            actor=HUMAN_PRIVACY_OWNER,
            created_at=clock.iso(),
            task_id="design-analytics-change",
            context_version=2,
        )
    )

    # Policy decisions are scoped to plan revision, so v2 re-evaluates every
    # required control rather than inheriting the v1 denial or an earlier allow.
    for decision in (
        policies.evaluate_privacy(
            "implement-coarse-analytics",
            ("utc_day", "click_count"),
            raw_client_identifiers_retained=False,
        ),
        policies.evaluate_change(
            "implement-coarse-analytics",
            is_breaking=False,
            declared_impact=ImpactClass.HIGH,
        ),
        policies.evaluate_privacy(
            "final-quality-approval",
            ("utc_day", "click_count"),
            raw_client_identifiers_retained=False,
        ),
        policies.evaluate_change(
            "final-quality-approval",
            is_breaking=False,
            declared_impact=ImpactClass.HIGH,
        ),
        policies.evaluate_evidence_retention(
            "final-quality-approval", retention_days=30, append_only=True
        ),
    ):
        engine.record_policy_decision(decision, actor=AGENT)

    # Re-applied approval gates: revised clarification, implementation, then
    # final quality. Each call resumes only human-authorized work.
    assert engine.run(actor=AGENT) is RunState.AWAITING_APPROVAL
    assert engine.task("clarify-analytics-privacy").state is TaskState.AWAITING_APPROVAL
    assert engine.decide_approval(
        _approval(
            ids,
            clock,
            "clarify-analytics-privacy",
            HUMAN_PRIVACY_OWNER,
            "revised v2 plans match the privacy clarification",
        )
    ) is RunState.AWAITING_APPROVAL
    assert engine.task("implement-coarse-analytics").state is TaskState.AWAITING_APPROVAL
    assert engine.decide_approval(
        _approval(
            ids,
            clock,
            "implement-coarse-analytics",
            HUMAN_PRIVACY_OWNER,
            "privacy and change-control allows reviewed for v2 implementation",
        )
    ) is RunState.AWAITING_APPROVAL
    assert engine.task("final-quality-approval").state is TaskState.AWAITING_APPROVAL
    assert engine.decide_approval(
        _approval(
            ids,
            clock,
            "final-quality-approval",
            HUMAN_QUALITY_OWNER,
            "v2 validation and contract evidence are complete and privacy-safe",
        )
    ) is RunState.SUCCEEDED

    # Scenario-level invariants make evidence generation fail loudly if the
    # selective blast radius or immutable history regresses.
    core_artifacts = [a for a in engine.artifacts if a.name == "core_scope_assurance"]
    analytics_artifacts = [a for a in engine.artifacts if a.name == "analytics_design"]
    assert len(engine.context_versions) == 2
    assert len(engine.plans) == 2
    assert core_artifacts and all(not artifact.stale for artifact in core_artifacts)
    assert [(artifact.version, artifact.stale) for artifact in analytics_artifacts] == [
        (1, True),
        (2, False),
    ]
    assert engine.metrics.replans == 1

    return ScenarioExecution(
        engine=engine,
        terminal_reason=(
            "Requirement v2 shipped UTC-day analytics after selective invalidation, "
            "re-applied controls, complete validation, and human quality approval."
        ),
        limitations=(
            "Daily buckets use the UTC date prefix of stored click timestamps; no timezone-local view is promised.",
            "The bounded aggregate adds no geolocation, fingerprinting, device analysis, or client identity.",
            "Existing recent click timestamps remain for baseline compatibility; v2 adds only a coarse aggregate.",
        ),
        reviewable_outputs={
            "analytics_repository": "app/repository.py",
            "analytics_contract": "app/models.py",
            "analytics_endpoint": "app/main.py",
            "api_tests": "tests/test_shortener_api.py",
            "service_tests": "tests/test_shortener_service.py",
            "scenario_tests": "tests/test_ambiguous_scenario.py",
        },
    )


SCENARIO = ScenarioSpec(
    scenario_id=SCENARIO_ID,
    title="Ambiguous analytics/privacy change with governed re-plan",
    execute=execute,
    dependencies=("s-02",),
    aliases=("ambiguous", "s03"),
    acceptable_states=frozenset({RunState.SUCCEEDED}),
)


__all__ = [
    "AMBIGUITIES",
    "RAW_REQUIREMENT",
    "RUN_ID",
    "SCENARIO",
    "SCENARIO_ID",
    "build_plan",
    "execute",
]
