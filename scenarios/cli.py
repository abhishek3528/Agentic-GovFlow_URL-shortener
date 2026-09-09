"""Command-line entry point for replaying governed scenarios."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from orchestrator.clock import deterministic_pair
from orchestrator.contracts import ContextVersion, Plan, RunResult
from orchestrator.planner import derive_plan
from scenarios.approvals import InteractiveApprovalProvider
from scenarios.runner import (
    DEFAULT_EVIDENCE_ROOT,
    ScenarioError,
    ScenarioRunner,
    default_runner,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="govflow",
        description="Replay governed SDLC scenarios and export audit evidence.",
    )
    subcommands = parser.add_subparsers(dest="command")

    listing = subcommands.add_parser("list", help="list available scenarios")
    listing.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    plan = subcommands.add_parser(
        "plan", help="derive a deterministic governed SDLC plan from a requirement"
    )
    plan.add_argument("requirement", help="plain-language engineering requirement")
    plan.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    run = subcommands.add_parser("run", help="run one scenario or the full chain")
    run.add_argument("scenario", help="scenario id/alias, or 'all'")
    run.add_argument(
        "--evidence-dir",
        type=Path,
        default=DEFAULT_EVIDENCE_ROOT,
        help="evidence root (default: repository evidence/ directory)",
    )
    run.add_argument(
        "--force",
        action="store_true",
        help="replay scenarios already completed by this runner invocation",
    )
    run.add_argument(
        "--interactive-approvals",
        action="store_true",
        help=(
            "pause for a real human decision at approval checkpoints "
            "(off by default so runs stay reproducible)"
        ),
    )
    run.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def _result_view(result: RunResult) -> dict[str, object]:
    return {
        "run_id": result.run_id,
        "scenario_id": result.scenario_id,
        "state": result.state.value,
        "terminal_reason": result.terminal_reason,
        "evidence_path": result.evidence_path,
        "metrics": result.metrics.model_dump(mode="json"),
    }


def _show_list(runner: ScenarioRunner, *, as_json: bool) -> None:
    rows = [
        {
            "scenario_id": spec.scenario_id,
            "title": spec.title,
            "dependencies": list(spec.dependencies),
            "aliases": list(spec.aliases),
        }
        for spec in runner.scenarios
    ]
    if as_json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return
    if not rows:
        print("No scenario implementations are available.")
        return
    for row in rows:
        dependencies = ", ".join(row["dependencies"]) or "none"
        print(f"{row['scenario_id']}: {row['title']} (depends on: {dependencies})")


def _show_results(results: Sequence[RunResult], *, as_json: bool) -> None:
    views = [_result_view(result) for result in results]
    if as_json:
        print(json.dumps(views, indent=2, sort_keys=True))
        return
    for result in results:
        print(
            f"{result.scenario_id}: {result.state.value} - {result.terminal_reason}\n"
            f"  evidence: {result.evidence_path}"
        )


def _show_plan(plan: Plan, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(plan.model_dump(mode="json"), indent=2, sort_keys=True))
        return

    print(f"Plan revision {plan.revision} (context v{plan.context_version})")
    for task in plan.tasks:
        dependencies = ", ".join(task.depends_on) or "none"
        print(f"\n{task.id}: {task.name}")
        print(f"  stage: {task.stage.value}")
        print(f"  capability: {task.capability}")
        print(f"  dependencies: {dependencies}")
        print(f"  impact: {task.impact.value}")
        if not task.entry_gates and not task.exit_gates:
            print("  gates: none")
            continue
        print("  gates:")
        for gate in (*task.entry_gates, *task.exit_gates):
            print(f"    {gate.kind.value}: {gate.id} - {gate.description}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``govflow`` command and return a process exit status."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 0

    if arguments.command == "plan":
        # The command is a reproducible demonstration artifact.  Its injected
        # clock starts from the same instant on every invocation so identical
        # requirement text produces byte-identical JSON across processes.
        clock, _ = deterministic_pair()
        normalized = " ".join(arguments.requirement.split())
        context = ContextVersion(
            version=1,
            raw_requirement=arguments.requirement,
            normalized_problem=normalized,
            created_at=clock.iso(),
        )
        plan = derive_plan(context, created_at=clock.iso())
        _show_plan(plan, as_json=arguments.json)
        return 0

    if (
        arguments.command == "run"
        and arguments.interactive_approvals
        and not sys.stdin.isatty()
    ):
        print(
            "govflow: --interactive-approvals requires a TTY on standard input; "
            "refusing to read from non-interactive input",
            file=sys.stderr,
        )
        return 2

    try:
        runner = default_runner(
            evidence_root=(
                arguments.evidence_dir
                if arguments.command == "run"
                else DEFAULT_EVIDENCE_ROOT
            ),
            approval_provider=(
                InteractiveApprovalProvider()
                if arguments.command == "run" and arguments.interactive_approvals
                else None
            ),
        )
        if arguments.command == "list":
            _show_list(runner, as_json=arguments.json)
            return 0

        if not runner.scenarios:
            raise ScenarioError("no scenario implementations are available")
        if arguments.scenario.lower() == "all":
            results = runner.run_all(force=arguments.force)
        else:
            results = runner.run_with_dependencies(
                arguments.scenario,
                force=arguments.force,
            )
        _show_results(results, as_json=arguments.json)

        specs = {spec.scenario_id: spec for spec in runner.scenarios}
        return 0 if all(
            result.state in specs[result.scenario_id].acceptable_states
            for result in results
        ) else 1
    except ScenarioError as exc:
        print(f"govflow: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - console script covers this path
    raise SystemExit(main())
