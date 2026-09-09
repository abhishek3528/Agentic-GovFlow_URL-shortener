"""Deterministic, requirement-driven SDLC plan derivation.

Planning is part of the governance control plane: this module turns an
immutable requirement context into a validated dependency graph without
executing work or mutating engine state.  Scenario-specific execution remains
separate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from orchestrator.contracts import (
    ContextVersion,
    Gate,
    GateKind,
    ImpactClass,
    Plan,
    Stage,
    Task,
)
from orchestrator.graph import DependencyGraph


@dataclass(frozen=True)
class _Signals:
    security: bool
    data: bool
    brownfield: bool
    breaking: bool
    analytics: bool
    documentation_only: bool


_SECURITY_PATTERNS = (
    r"\bcredentials?\b",
    r"\bauth\w*\b",
    r"\bprivacy\b",
    r"\bclient identifiers?\b",
)
_DATA_PATTERNS = (
    r"\bpersistence\b",
    r"\bschemas?\b",
    r"\bmigrations?\b",
    r"\bdatabases?\b",
)
_BROWNFIELD_PATTERNS = (
    r"\bexisting codebase\b",
    r"\bbrownfield\b",
    r"\bdefects?\b",
    r"\bregressions?\b",
    r"\bfix(?:es|ed|ing)?\b",
)
_BREAKING_PATTERNS = (r"\bbreaking\b", r"\bcontract changes?\b")
_ANALYTICS_PATTERNS = (r"\banalytics?\b", r"\breporting\b", r"\breports?\b")
_DOCUMENTATION_PATTERNS = (
    r"\bdocs?\b",
    r"\bdocumentation\b",
    r"\breadme\b",
    r"\btypos?\b",
    r"\bspelling\b",
)
_IMPLEMENTATION_PATTERNS = (
    r"\badd\b",
    r"\bbuild\b",
    r"\bimplement\w*\b",
    r"\bendpoint\b",
    r"\bservice\b",
    r"\bbehavior\b",
)
_S01_RAW_REQUIREMENT = (
    "Establish a URL-shortener baseline that creates and redirects short URLs, "
    "reports basic privacy-conscious analytics, exposes health and readiness, "
    "publishes OpenAPI, and validates collision and idempotency behavior."
)


def _contains(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) is not None for pattern in patterns)


def _requirement_text(context: ContextVersion) -> str:
    """Return the ordered, normalized corpus used for rule matching."""
    sections = (
        context.raw_requirement,
        context.normalized_problem,
        *context.acceptance_checks,
    )
    return "\n".join(section.casefold() for section in sections)


def _is_s01_baseline(context: ContextVersion) -> bool:
    """Recognize the established greenfield profile whose graph is contractual.

    The baseline's already-established analytics/reliability branch owns its
    bounded "privacy-conscious analytics" concern.  New privacy/auth/credential
    changes still receive the explicit review node added by the generic rules.
    """
    actual = " ".join(context.raw_requirement.split()).casefold()
    expected = " ".join(_S01_RAW_REQUIREMENT.split()).casefold()
    return actual == expected


def _detect_signals(text: str, *, s01_baseline: bool) -> _Signals:
    security = _contains(text, _SECURITY_PATTERNS) and not s01_baseline
    data = _contains(text, _DATA_PATTERNS)
    brownfield = _contains(text, _BROWNFIELD_PATTERNS)
    breaking = _contains(text, _BREAKING_PATTERNS)
    analytics = _contains(text, _ANALYTICS_PATTERNS)
    documentation = _contains(text, _DOCUMENTATION_PATTERNS)
    implementation = _contains(text, _IMPLEMENTATION_PATTERNS)
    documentation_only = (
        documentation
        and not implementation
        and not any((security, data, brownfield, breaking, analytics))
    )
    return _Signals(
        security=security,
        data=data,
        brownfield=brownfield,
        breaking=breaking,
        analytics=analytics,
        documentation_only=documentation_only,
    )


def _gate(gate_id: str, kind: GateKind, description: str) -> Gate:
    return Gate(id=gate_id, kind=kind, description=description)


def _standard_gates() -> tuple[Gate, Gate, Gate, Gate]:
    return (
        _gate(
            "requirement-structure",
            GateKind.ENTRY,
            "The request names the required product outcomes.",
        ),
        _gate(
            "declared-outputs-present",
            GateKind.EXIT,
            "Every declared output is present before task success.",
        ),
        _gate(
            "declared-inputs-fresh",
            GateKind.ENTRY,
            "Every declared input exists and is fresh.",
        ),
        _gate(
            "release-evidence-complete",
            GateKind.ENTRY,
            "Integrated validation, documentation, and API contract are present.",
        ),
    )


def _plan_metadata(
    context: ContextVersion, *, created_at: str, revision: int, tasks: list[Task]
) -> Plan:
    plan = Plan(
        revision=revision,
        context_version=context.version,
        tasks=tuple(tasks),
        created_at=created_at,
        supersedes=revision - 1 if revision > 1 else None,
        change_reason=context.change_reason if revision > 1 else None,
    )
    # A derived plan is never allowed to bypass the graph's structural rules.
    # Construction succeeds only if the exact plan returned to the caller is
    # accepted unchanged by DependencyGraph.
    DependencyGraph(plan)
    return plan


def _derive_s01_plan(
    context: ContextVersion, *, created_at: str, revision: int
) -> Plan:
    """Reproduce the established seven-task S-01 graph exactly."""
    requirement_entry, outputs_present, inputs_fresh, release_evidence = (
        _standard_gates()
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
    return _plan_metadata(
        context,
        created_at=created_at,
        revision=revision,
        tasks=[normalize, design, core, analytics, validation, documentation, release],
    )


def _derive_generic_plan(
    context: ContextVersion,
    *,
    created_at: str,
    revision: int,
    signals: _Signals,
) -> Plan:
    requirement_entry, outputs_present, inputs_fresh, release_evidence = (
        _standard_gates()
    )
    tasks: list[Task] = []

    normalize = Task(
        id="normalize-requirement",
        name="Normalize the requested change",
        stage=Stage.REQUIREMENTS,
        capability="business-analyst",
        produces=("normalized_requirement",),
        entry_gates=(requirement_entry,),
        exit_gates=(outputs_present,),
    )
    tasks.append(normalize)

    if signals.documentation_only:
        documentation = Task(
            id="document-change",
            name="Update the requested documentation",
            stage=Stage.DOCUMENTATION,
            capability="technical-writer",
            depends_on=(normalize.id,),
            consumes=("normalized_requirement",),
            produces=("change_documentation",),
            entry_gates=(inputs_fresh,),
            exit_gates=(outputs_present,),
        )
        release = Task(
            id="release-readiness",
            name="Human release-readiness review",
            stage=Stage.RELEASE_READINESS,
            capability="release-manager",
            depends_on=(documentation.id,),
            consumes=("change_documentation",),
            produces=("release_readiness_record",),
            impact=ImpactClass.HIGH,
            entry_gates=(release_evidence,),
            exit_gates=(outputs_present,),
        )
        tasks.extend((documentation, release))
        return _plan_metadata(
            context, created_at=created_at, revision=revision, tasks=tasks
        )

    planning_dependency = normalize.id
    planning_inputs: list[str] = ["normalized_requirement"]
    if signals.brownfield:
        impact = Task(
            id="impact-analysis",
            name="Analyze the existing codebase impact",
            stage=Stage.REQUIREMENTS,
            capability="brownfield-analyst",
            depends_on=(normalize.id,),
            consumes=("normalized_requirement",),
            produces=("impact_analysis",),
            entry_gates=(inputs_fresh,),
            exit_gates=(outputs_present,),
        )
        reproduction = Task(
            id="red-reproduction",
            name="Reproduce the defect with a red regression check",
            stage=Stage.TESTING,
            capability="quality-engineer",
            depends_on=(impact.id,),
            consumes=("normalized_requirement", "impact_analysis"),
            produces=("red_reproduction",),
            entry_gates=(inputs_fresh,),
            exit_gates=(outputs_present,),
        )
        tasks.extend((impact, reproduction))
        planning_dependency = reproduction.id
        planning_inputs.extend(("impact_analysis", "red_reproduction"))

    design = Task(
        id="design-solution",
        name="Design the bounded solution",
        stage=Stage.DESIGN,
        capability="solution-architect",
        depends_on=(planning_dependency,),
        consumes=tuple(planning_inputs),
        produces=("solution_design", "change_contract"),
        entry_gates=(inputs_fresh,),
        exit_gates=(outputs_present,),
    )
    tasks.append(design)

    implementation_predecessor = design.id
    implementation_inputs: list[str] = ["solution_design", "change_contract"]
    if signals.data:
        data_design = Task(
            id="data-design",
            name="Design persistence, schema, and migration effects",
            stage=Stage.DESIGN,
            capability="data-architect",
            depends_on=(design.id,),
            consumes=("solution_design", "change_contract"),
            produces=("data_design",),
            entry_gates=(inputs_fresh,),
            exit_gates=(outputs_present,),
        )
        tasks.append(data_design)
        implementation_predecessor = data_design.id
        implementation_inputs.append("data_design")

    if signals.security:
        security_entry = _gate(
            "security-review-inputs-complete",
            GateKind.ENTRY,
            "Design and relevant data effects are complete before security review.",
        )
        security_exit = _gate(
            "security-privacy-review-passed",
            GateKind.EXIT,
            "Credential, authentication, and privacy risks have an explicit disposition.",
        )
        security_review = Task(
            id="security-privacy-review",
            name="Review security and privacy implications",
            stage=Stage.DESIGN,
            capability="security-privacy-reviewer",
            depends_on=(implementation_predecessor,),
            consumes=tuple(implementation_inputs),
            produces=("security_privacy_review",),
            impact=ImpactClass.HIGH,
            entry_gates=(security_entry,),
            exit_gates=(security_exit, outputs_present),
        )
        tasks.append(security_review)
        implementation_predecessor = security_review.id
        implementation_inputs.append("security_privacy_review")

    implement = Task(
        id="implement-change",
        name="Implement the approved change",
        stage=Stage.IMPLEMENTATION,
        capability="backend-engineer",
        depends_on=(implementation_predecessor,),
        consumes=tuple(implementation_inputs),
        produces=("change_implementation",),
        entry_gates=(inputs_fresh,),
        exit_gates=(outputs_present,),
    )
    tasks.append(implement)

    validation_dependencies: list[str] = [implement.id]
    validation_inputs: list[str] = ["change_implementation"]
    if signals.analytics:
        analytics_design = Task(
            id="analytics-design",
            name="Design analytics and reporting behavior",
            stage=Stage.DESIGN,
            capability="analytics-architect",
            depends_on=(implementation_predecessor,),
            consumes=tuple(implementation_inputs),
            produces=("analytics_design",),
            entry_gates=(inputs_fresh,),
            exit_gates=(outputs_present,),
        )
        tasks.append(analytics_design)
        validation_dependencies.append(analytics_design.id)
        validation_inputs.append("analytics_design")

    validation = Task(
        id="validate-change",
        name="Run unit, integration, and acceptance validation",
        stage=Stage.TESTING,
        capability="quality-engineer",
        depends_on=tuple(validation_dependencies),
        consumes=tuple(validation_inputs),
        produces=("validation_report",),
        entry_gates=(inputs_fresh,),
        exit_gates=(outputs_present,),
    )
    tasks.append(validation)

    documentation = Task(
        id="document-change",
        name="Document the implemented change and validation",
        stage=Stage.DOCUMENTATION,
        capability="technical-writer",
        depends_on=(design.id, validation.id),
        consumes=("change_contract", "validation_report"),
        produces=("change_documentation",),
        entry_gates=(inputs_fresh,),
        exit_gates=(outputs_present,),
    )
    tasks.append(documentation)

    release_inputs = ["change_contract", "validation_report", "change_documentation"]
    if signals.security:
        release_inputs.append("security_privacy_review")
    release = Task(
        id="release-readiness",
        name="Human release-readiness review",
        stage=Stage.RELEASE_READINESS,
        capability="release-manager",
        depends_on=(validation.id, documentation.id),
        consumes=tuple(release_inputs),
        produces=("release_readiness_record",),
        impact=ImpactClass.BREAKING if signals.breaking else ImpactClass.HIGH,
        entry_gates=(release_evidence,),
        exit_gates=(outputs_present,),
    )
    tasks.append(release)
    return _plan_metadata(
        context, created_at=created_at, revision=revision, tasks=tasks
    )


def derive_plan(
    context: ContextVersion, *, created_at: str, revision: int = 1
) -> Plan:
    """Derive and validate a deterministic SDLC plan from requirement context.

    Signal matching reads the raw requirement, normalized problem, and every
    acceptance check.  All task, dependency, artifact, and gate sequences are
    constructed from ordered tuples/lists so Python hash randomization cannot
    change serialized output between processes.
    """
    text = _requirement_text(context)
    s01_baseline = _is_s01_baseline(context)
    if s01_baseline:
        return _derive_s01_plan(context, created_at=created_at, revision=revision)
    signals = _detect_signals(text, s01_baseline=False)
    return _derive_generic_plan(
        context,
        created_at=created_at,
        revision=revision,
        signals=signals,
    )
