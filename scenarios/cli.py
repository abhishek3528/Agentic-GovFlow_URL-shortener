"""Command-line entry point for replaying governed scenarios."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from orchestrator.contracts import RunResult
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


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``govflow`` command and return a process exit status."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 0

    try:
        runner = default_runner(
            evidence_root=(
                arguments.evidence_dir
                if arguments.command == "run"
                else DEFAULT_EVIDENCE_ROOT
            )
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
