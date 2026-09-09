from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.contracts import ContextVersion, ImpactClass
from orchestrator.graph import DependencyGraph
from orchestrator.planner import derive_plan
from scenarios.cli import main


CREATED_AT = "2026-01-01T00:00:01+00:00"


def _context(
    raw_requirement: str,
    *,
    normalized_problem: str = "",
    acceptance_checks: tuple[str, ...] = (),
) -> ContextVersion:
    return ContextVersion(
        version=1,
        raw_requirement=raw_requirement,
        normalized_problem=normalized_problem,
        acceptance_checks=acceptance_checks,
        created_at="2026-01-01T00:00:00+00:00",
    )


def _task_ids(plan) -> tuple[str, ...]:
    return tuple(task.id for task in plan.tasks)


def _plan_shape(plan) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            task.id,
            task.stage,
            task.capability,
            task.depends_on,
            task.consumes,
            task.produces,
            task.impact,
        )
        for task in plan.tasks
    )


def test_trivial_documentation_change_derives_a_small_plan_without_security() -> None:
    plan = derive_plan(
        _context("Make a trivial doc change to correct README wording."),
        created_at=CREATED_AT,
    )

    assert _task_ids(plan) == (
        "normalize-requirement",
        "document-change",
        "release-readiness",
    )
    assert plan.task_by_id("security-privacy-review") is None
    DependencyGraph(plan)


def test_credential_change_adds_gated_security_review_and_gated_release() -> None:
    plan = derive_plan(
        _context("Add secure handling for service credentials."),
        created_at=CREATED_AT,
    )

    security = plan.task_by_id("security-privacy-review")
    release = plan.task_by_id("release-readiness")
    assert security is not None
    assert security.requires_approval
    assert [gate.id for gate in security.entry_gates] == [
        "security-review-inputs-complete"
    ]
    assert [gate.id for gate in security.exit_gates] == [
        "security-privacy-review-passed",
        "declared-outputs-present",
    ]
    assert release is not None
    assert release.requires_approval
    assert "security_privacy_review" in release.consumes
    assert len(plan.tasks) > 3
    DependencyGraph(plan)


def test_a_paraphrased_requirement_derives_a_comparable_plan() -> None:
    """A deriver answers the meaning; a lookup table answers the string."""
    requirement = (
        "Establish a URL-shortener baseline that creates and redirects short URLs, "
        "reports basic privacy-conscious analytics, exposes health and readiness, "
        "publishes OpenAPI, and validates collision and idempotency behavior."
    )
    paraphrase = (
        "Build a URL shortening service with create and redirect behavior, "
        "privacy-conscious link analytics, health and readiness endpoints, an "
        "OpenAPI contract, and collision plus idempotency validation."
    )

    original = derive_plan(_context(requirement), created_at=CREATED_AT)
    reworded = derive_plan(_context(paraphrase), created_at=CREATED_AT)
    extended = derive_plan(_context(requirement + " Please."), created_at=CREATED_AT)

    assert _plan_shape(original) == _plan_shape(reworded)
    assert _plan_shape(original) == _plan_shape(extended)
    DependencyGraph(original)
    DependencyGraph(reworded)
    DependencyGraph(extended)


def test_fix_without_a_defect_signal_does_not_add_red_reproduction() -> None:
    plan = derive_plan(
        _context("Fix a typo in the operator guide."), created_at=CREATED_AT
    )

    assert _task_ids(plan) == (
        "normalize-requirement",
        "document-change",
        "release-readiness",
    )
    assert plan.task_by_id("red-reproduction") is None


def test_documentation_only_defect_word_does_not_add_red_reproduction() -> None:
    plan = derive_plan(
        _context("Correct an incorrect spelling in the operator documentation."),
        created_at=CREATED_AT,
    )

    assert plan.task_by_id("red-reproduction") is None


def test_migration_vocabulary_adds_data_design() -> None:
    plan = derive_plan(
        _context("Migrate the links table to add an index and backfill existing rows."),
        created_at=CREATED_AT,
    )

    assert plan.task_by_id("data-design") is not None


@pytest.mark.parametrize(
    "wording",
    (
        "Make a backwards-incompatible API update.",
        "Make a backward incompatible API update.",
        "Make an incompatible change to the API.",
        "Make a contract change to the redirect endpoint.",
    ),
)
def test_breaking_vocabulary_marks_release_as_breaking(wording: str) -> None:
    plan = derive_plan(_context(wording), created_at=CREATED_AT)

    assert plan.task_by_id("release-readiness").impact is ImpactClass.BREAKING


def test_task_count_increases_with_change_complexity() -> None:
    documentation = derive_plan(
        _context("Fix a typo in the operator guide."), created_at=CREATED_AT
    )
    defect = derive_plan(
        _context("Fix a collision bug in the existing codebase."),
        created_at=CREATED_AT,
    )
    complex_change = derive_plan(
        _context(
            "Fix a collision bug, migrate the links table, and make a "
            "backward incompatible contract change."
        ),
        created_at=CREATED_AT,
    )

    assert len(documentation.tasks) < len(defect.tasks) < len(complex_change.tasks)


@pytest.mark.parametrize(
    ("context", "expected_task"),
    (
        (_context("Add database persistence and a schema migration."), "data-design"),
        (
            _context("Fix a regression in the existing codebase."),
            "impact-analysis",
        ),
        (
            _context(
                "Improve the service.",
                acceptance_checks=("Analytics reporting has deterministic totals.",),
            ),
            "analytics-design",
        ),
    ),
)
def test_rule_signals_add_their_expected_nodes(context, expected_task: str) -> None:
    plan = derive_plan(context, created_at=CREATED_AT)

    assert plan.task_by_id(expected_task) is not None
    DependencyGraph(plan)


def test_brownfield_prerequisites_are_ahead_of_solution_planning() -> None:
    plan = derive_plan(
        _context("Fix a defect and regression in the existing codebase."),
        created_at=CREATED_AT,
    )

    assert _task_ids(plan)[:4] == (
        "normalize-requirement",
        "impact-analysis",
        "red-reproduction",
        "design-solution",
    )
    assert plan.task_by_id("design-solution").depends_on == ("red-reproduction",)


def test_analytics_branch_forks_from_implementation_predecessor_and_joins() -> None:
    plan = derive_plan(
        _context("Add analytics reporting."),
        created_at=CREATED_AT,
    )

    implement = plan.task_by_id("implement-change")
    analytics = plan.task_by_id("analytics-design")
    validation = plan.task_by_id("validate-change")
    assert implement is not None and analytics is not None and validation is not None
    assert implement.depends_on == analytics.depends_on
    assert validation.depends_on == (implement.id, analytics.id)
    assert validation.consumes == ("change_implementation", "analytics_design")


def test_breaking_contract_change_marks_release_as_breaking() -> None:
    plan = derive_plan(
        _context("Make a breaking contract change to the redirect endpoint."),
        created_at=CREATED_AT,
    )

    release = plan.task_by_id("release-readiness")
    assert release is not None
    assert release.impact is ImpactClass.BREAKING
    assert release.requires_approval


def test_signal_detection_reads_normalized_problem_and_acceptance_checks() -> None:
    plan = derive_plan(
        _context(
            "Improve link handling.",
            normalized_problem="Store the result in a database.",
            acceptance_checks=("Client identifiers are prohibited by privacy policy.",),
        ),
        created_at=CREATED_AT,
    )

    assert plan.task_by_id("data-design") is not None
    assert plan.task_by_id("security-privacy-review") is not None


def test_derived_plan_is_identical_in_separate_hash_seeded_processes() -> None:
    script = "\n".join(
        (
            "from orchestrator.contracts import ContextVersion",
            "from orchestrator.planner import derive_plan",
            "context = ContextVersion(version=1, raw_requirement='Add analytics reporting with credentials and database persistence.', normalized_problem='Change an existing codebase safely.', acceptance_checks=('No regression is permitted.',), created_at='2026-01-01T00:00:00+00:00')",
            f"plan = derive_plan(context, created_at={CREATED_AT!r})",
            "print(plan.model_dump_json())",
        )
    )
    root = Path(__file__).resolve().parents[1]

    outputs: list[str] = []
    for seed in ("7", "91"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]


def test_plan_cli_prints_tasks_capabilities_dependencies_and_gates(capsys) -> None:
    assert main(["plan", "add rate limiting to the redirect endpoint"]) == 0

    output = capsys.readouterr().out
    assert "normalize-requirement" in output
    assert "capability: business-analyst" in output
    assert "dependencies:" in output
    assert "gates:" in output
    assert "release-readiness" in output
