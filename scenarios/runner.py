"""Shared scenario execution and reproducible evidence export.

Scenario modules own their plans, executors, and governed decisions.  This
module supplies the small amount of plumbing they all share: registration,
dependency ordering, conversion of a completed engine into ``RunResult``, and
serialization of an evidence bundle.  It deliberately does not write events or
change engine state; governance remains the engine's responsibility.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from orchestrator.contracts import (
    ContextVersion,
    Decision,
    EventType,
    RunResult,
    RunState,
    TERMINAL_RUN_STATES,
)
from orchestrator.engine import OrchestrationEngine
from scenarios.approvals import ApprovalProvider, ScriptedApprovalProvider


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_ROOT = PROJECT_ROOT / "evidence"
EVIDENCE_SCHEMA_VERSION = 1

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ScenarioError(RuntimeError):
    """A scenario could not be selected, executed, or finalized safely."""


class EvidenceExportError(ScenarioError):
    """A completed run could not be represented as a valid evidence bundle."""


@dataclass(frozen=True)
class ScenarioContext:
    """Read-only context supplied to a scenario implementation.

    ``prior_results`` makes the S-01 -> S-02 -> S-03 relationship explicit
    without giving a scenario permission to alter a prior run's evidence.
    """

    workspace: Path
    evidence_root: Path
    prior_results: Mapping[str, RunResult]
    approval_provider: ApprovalProvider = field(default_factory=ScriptedApprovalProvider)


@dataclass(frozen=True)
class ScenarioExecution:
    """The governed engine plus scenario-owned evidence needed at finalization."""

    engine: OrchestrationEngine
    context_versions: tuple[ContextVersion, ...] = ()
    decisions: tuple[Decision, ...] = ()
    terminal_reason: str | None = None
    limitations: tuple[str, ...] = ()
    reviewable_outputs: Mapping[str, str] = field(default_factory=dict)


ScenarioCallable = Callable[[ScenarioContext], ScenarioExecution | OrchestrationEngine]


@dataclass(frozen=True)
class ScenarioSpec:
    """A registered scenario and its prerequisites."""

    scenario_id: str
    title: str
    execute: ScenarioCallable
    dependencies: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    acceptable_states: frozenset[RunState] = frozenset({RunState.SUCCEEDED})

    def __post_init__(self) -> None:
        _validate_safe_name(self.scenario_id, "scenario id")
        if not self.title.strip():
            raise ValueError("scenario title must not be empty")
        if not callable(self.execute):
            raise TypeError("scenario execute must be callable")
        if not self.acceptable_states:
            raise ValueError("a scenario must declare at least one acceptable state")
        if not self.acceptable_states.issubset(TERMINAL_RUN_STATES):
            raise ValueError("acceptable scenario states must be terminal")


def _validate_safe_name(value: str, label: str) -> None:
    if not _SAFE_NAME.fullmatch(value) or value in {".", ".."}:
        raise ValueError(
            f"{label} must be a safe path component containing only letters, "
            "digits, '.', '_' or '-'"
        )


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_jsonable(item) for item in value), key=str)
    if isinstance(value, Path):
        return value.as_posix()
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    ) + "\n"


def _terminal_reason(engine: OrchestrationEngine) -> str:
    completed = [event for event in engine.events if event.type is EventType.RUN_COMPLETED]
    if completed:
        return completed[-1].summary or str(completed[-1].payload.get("state", "completed"))
    return f"run ended in {engine.run_state.value}"


def build_run_result(
    scenario_id: str,
    execution: ScenarioExecution,
    *,
    evidence_path: str | None = None,
) -> RunResult:
    """Project a terminal engine execution into the frozen result contract."""

    _validate_safe_name(scenario_id, "scenario id")
    engine = execution.engine
    if engine.run_state not in TERMINAL_RUN_STATES:
        raise ScenarioError(
            f"scenario '{scenario_id}' stopped in non-terminal state "
            f"'{engine.run_state.value}'"
        )
    if engine.started_at is None or engine.completed_at is None:
        raise ScenarioError(
            f"scenario '{scenario_id}' has no complete start/end event pair"
        )

    # Future engine versions may expose these collections directly.  Explicit
    # scenario values take precedence and keep this runner compatible with the
    # current frozen contract without inspecting engine internals.
    contexts = execution.context_versions or tuple(
        getattr(engine, "context_versions", ())
    )
    decisions = execution.decisions or tuple(getattr(engine, "decisions", ()))
    return RunResult(
        run_id=engine.run_id,
        scenario_id=scenario_id,
        state=engine.run_state,
        terminal_reason=execution.terminal_reason or _terminal_reason(engine),
        started_at=engine.started_at,
        completed_at=engine.completed_at,
        context_versions=contexts,
        plans=engine.plans,
        artifacts=engine.artifacts,
        decisions=decisions,
        metrics=engine.metrics,
        evidence_path=evidence_path,
        limitations=execution.limitations,
    )


class EvidenceExporter:
    """Write a deterministic, reviewer-oriented bundle for one completed run."""

    def __init__(self, root: str | Path = DEFAULT_EVIDENCE_ROOT) -> None:
        self.root = Path(root).resolve()

    def export(self, scenario_id: str, execution: ScenarioExecution) -> RunResult:
        """Export ``execution`` and return its result with ``evidence_path`` set.

        Artifact contents are intentionally not copied.  The bundle records
        their provenance, hashes, and declared review paths; this avoids
        turning an audit export into an uncontrolled data-exfiltration path.
        """

        engine = execution.engine
        _validate_safe_name(engine.run_id, "run id")
        bundle_dir = (self.root / engine.run_id).resolve()
        if bundle_dir.parent != self.root:
            raise EvidenceExportError("run id escapes the configured evidence root")

        evidence_path = self._display_path(bundle_dir)
        result = build_run_result(
            scenario_id,
            execution,
            evidence_path=evidence_path,
        )
        bundle_dir.mkdir(parents=True, exist_ok=True)

        graph = {
            "revisions": [self._graph_revision(plan) for plan in result.plans],
        }
        controls = {
            "gate_results": engine.gate_results,
            "policy_decisions": engine.policy_decisions,
            "approvals": engine.approvals,
        }
        files: dict[str, str] = {
            "result.json": _canonical_json(result),
            "context_versions.json": _canonical_json(result.context_versions),
            "plans.json": _canonical_json(result.plans),
            "graph.json": _canonical_json(graph),
            "artifacts.json": _canonical_json(result.artifacts),
            "decisions.json": _canonical_json(result.decisions),
            "controls.json": _canonical_json(controls),
            "metrics.json": _canonical_json(result.metrics),
            "events.jsonl": "".join(
                event.model_dump_json() + "\n" for event in engine.events
            ),
        }
        for name, content in files.items():
            self._atomic_write(bundle_dir / name, content)

        descriptions = {
            "result.json": "terminal scenario result and evidence pointer",
            "context_versions.json": "original and revised requirement contexts",
            "plans.json": "versioned task plans with gates and I/O contracts",
            "graph.json": "dependency and artifact-flow graph by plan revision",
            "artifacts.json": "artifact provenance, freshness, and content hashes",
            "decisions.json": "attributed engineering decision lineage",
            "controls.json": "gate, policy, and human-approval outcomes",
            "metrics.json": "reliability metrics projected from events",
            "events.jsonl": "append-only correlated event history",
        }
        index = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "run_id": result.run_id,
            "scenario_id": result.scenario_id,
            "state": result.state.value,
            "terminal_reason": result.terminal_reason,
            "files": [
                {
                    "path": name,
                    "description": descriptions[name],
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }
                for name, content in files.items()
            ],
            "reviewable_outputs": dict(sorted(execution.reviewable_outputs.items())),
        }
        self._atomic_write(bundle_dir / "index.json", _canonical_json(index))
        return result

    def _display_path(self, bundle_dir: Path) -> str:
        try:
            return bundle_dir.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            return bundle_dir.as_posix()

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8", newline="\n")
            temporary.replace(path)
        except OSError as exc:
            raise EvidenceExportError(f"could not write evidence file '{path}': {exc}") from exc
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _graph_revision(plan: Any) -> dict[str, Any]:
        return {
            "revision": plan.revision,
            "context_version": plan.context_version,
            "supersedes": plan.supersedes,
            "nodes": [
                {
                    "id": task.id,
                    "name": task.name,
                    "stage": task.stage.value,
                    "capability": task.capability,
                    "state": task.state.value,
                    "impact": task.impact.value,
                    "consumes": list(task.consumes),
                    "produces": list(task.produces),
                    "entry_gates": [gate.id for gate in task.entry_gates],
                    "exit_gates": [gate.id for gate in task.exit_gates],
                }
                for task in plan.tasks
            ],
            "dependency_edges": [
                {"from": dependency, "to": task.id}
                for task in plan.tasks
                for dependency in task.depends_on
            ],
            "artifact_flows": [
                {"artifact": artifact, "producer": task.id}
                for task in plan.tasks
                for artifact in task.produces
            ],
        }


class ScenarioRunner:
    """Run registered scenarios in dependency order and export their evidence."""

    def __init__(
        self,
        scenarios: Iterable[ScenarioSpec] = (),
        *,
        workspace: str | Path = PROJECT_ROOT,
        evidence_root: str | Path = DEFAULT_EVIDENCE_ROOT,
        approval_provider: ApprovalProvider | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.exporter = EvidenceExporter(evidence_root)
        self.approval_provider = (
            approval_provider
            if approval_provider is not None
            else ScriptedApprovalProvider()
        )
        self._scenarios: dict[str, ScenarioSpec] = {}
        self._aliases: dict[str, str] = {}
        self._results: dict[str, RunResult] = {}
        for scenario in scenarios:
            self.register(scenario)

    @property
    def scenarios(self) -> tuple[ScenarioSpec, ...]:
        return tuple(self._scenarios.values())

    @property
    def results(self) -> Mapping[str, RunResult]:
        return MappingProxyType(dict(self._results))

    def register(self, scenario: ScenarioSpec) -> None:
        key = scenario.scenario_id.lower()
        names = (scenario.scenario_id, *scenario.aliases)
        normalized = tuple(name.lower() for name in names)
        conflicts = [name for name in normalized if name in self._aliases]
        if key in self._scenarios or conflicts:
            duplicate = key if key in self._scenarios else conflicts[0]
            raise ScenarioError(f"duplicate scenario name or alias '{duplicate}'")
        self._scenarios[key] = scenario
        for name in normalized:
            self._aliases[name] = key

    def resolve(self, name: str) -> ScenarioSpec:
        key = self._aliases.get(name.lower())
        if key is None:
            available = ", ".join(spec.scenario_id for spec in self.scenarios) or "<none>"
            raise ScenarioError(f"unknown scenario '{name}'; available: {available}")
        return self._scenarios[key]

    def run(self, name: str, *, force: bool = False) -> RunResult:
        """Run one scenario after its prerequisites and return its result."""

        spec = self.resolve(name)
        self._run_spec(spec, force=force, active=())
        return self._results[spec.scenario_id]

    def run_with_dependencies(
        self, name: str, *, force: bool = False
    ) -> tuple[RunResult, ...]:
        """Run a target and return newly executed prerequisite/result records."""

        before = dict(self._results)
        target = self.resolve(name)
        self._run_spec(target, force=force, active=())
        changed = [
            result
            for scenario_id, result in self._results.items()
            if force or scenario_id not in before or before[scenario_id] != result
        ]
        return tuple(changed)

    def run_all(self, *, force: bool = False) -> tuple[RunResult, ...]:
        results: list[RunResult] = []
        for spec in self.scenarios:
            self._run_spec(spec, force=force, active=())
            result = self._results[spec.scenario_id]
            results.append(result)
            if result.state not in spec.acceptable_states:
                break
        return tuple(results)

    def _run_spec(
        self,
        spec: ScenarioSpec,
        *,
        force: bool,
        active: tuple[str, ...],
    ) -> None:
        if spec.scenario_id in active:
            cycle = " -> ".join((*active, spec.scenario_id))
            raise ScenarioError(f"scenario dependency cycle: {cycle}")
        if not force and spec.scenario_id in self._results:
            return

        chain = (*active, spec.scenario_id)
        for dependency_name in spec.dependencies:
            dependency = self.resolve(dependency_name)
            self._run_spec(dependency, force=force, active=chain)
            dependency_result = self._results[dependency.scenario_id]
            if dependency_result.state not in dependency.acceptable_states:
                raise ScenarioError(
                    f"scenario '{spec.scenario_id}' cannot start: dependency "
                    f"'{dependency.scenario_id}' ended in "
                    f"'{dependency_result.state.value}'"
                )

        context = ScenarioContext(
            workspace=self.workspace,
            evidence_root=self.exporter.root,
            prior_results=MappingProxyType(dict(self._results)),
            approval_provider=self.approval_provider,
        )
        try:
            raw_execution = spec.execute(context)
        except Exception as exc:
            if isinstance(exc, ScenarioError):
                raise
            raise ScenarioError(
                f"scenario '{spec.scenario_id}' raised {type(exc).__name__}: {exc}"
            ) from exc
        execution = (
            raw_execution
            if isinstance(raw_execution, ScenarioExecution)
            else ScenarioExecution(engine=raw_execution)
            if isinstance(raw_execution, OrchestrationEngine)
            else None
        )
        if execution is None:
            raise ScenarioError(
                f"scenario '{spec.scenario_id}' returned unsupported value "
                f"'{type(raw_execution).__name__}'"
            )
        for completed_id, completed in self._results.items():
            if completed.run_id == execution.engine.run_id and completed_id != spec.scenario_id:
                raise ScenarioError(
                    f"scenario '{spec.scenario_id}' reused run id "
                    f"'{execution.engine.run_id}' from '{completed_id}'"
                )
        result = self.exporter.export(spec.scenario_id, execution)
        self._results[spec.scenario_id] = result


_BUILTIN_MODULES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("scenarios.greenfield", "s-01", "Greenfield core service", ("greenfield", "s01")),
    ("scenarios.brownfield", "s-02", "Brownfield reliability change", ("brownfield", "s02")),
    ("scenarios.ambiguous", "s-03", "Ambiguous analytics/privacy change", ("ambiguous", "s03")),
)


def load_builtin_scenarios() -> tuple[ScenarioSpec, ...]:
    """Discover the three scenario modules without hiding import failures.

    A module can expose ``SCENARIO``/``get_scenario`` for full metadata, or a
    conventional ``execute``/``run_scenario``/``run`` callable.  Missing
    modules are skipped so runner and CLI development remains independently
    testable while scenario implementations land.
    """

    found: list[ScenarioSpec] = []
    dependency_by_id = {"s-01": (), "s-02": ("s-01",), "s-03": ("s-02",)}
    for module_name, scenario_id, title, aliases in _BUILTIN_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                continue
            raise

        candidate = getattr(module, "SCENARIO", None)
        if candidate is None:
            factory = getattr(module, "get_scenario", None)
            candidate = factory() if callable(factory) else None
        if isinstance(candidate, ScenarioSpec):
            spec = candidate
            # The repository contract makes the built-in chain strictly
            # sequential.  A module may add prerequisites, but cannot silently
            # omit its preceding scenario.
            required = dependency_by_id[scenario_id]
            if required and not set(required).issubset(spec.dependencies):
                spec = replace(
                    spec,
                    dependencies=tuple(dict.fromkeys((*required, *spec.dependencies))),
                )
        else:
            execute = next(
                (
                    value
                    for name in ("execute", "run_scenario", "run")
                    if callable(value := getattr(module, name, None))
                ),
                None,
            )
            if execute is None:
                raise ScenarioError(
                    f"built-in module '{module_name}' exposes no ScenarioSpec or execute callable"
                )
            spec = ScenarioSpec(
                scenario_id=scenario_id,
                title=title,
                execute=execute,
                dependencies=dependency_by_id[scenario_id],
                aliases=aliases,
            )
        found.append(spec)
    return tuple(found)


def default_runner(
    *,
    workspace: str | Path = PROJECT_ROOT,
    evidence_root: str | Path = DEFAULT_EVIDENCE_ROOT,
    approval_provider: ApprovalProvider | None = None,
) -> ScenarioRunner:
    """Create the standard runner populated with all available built-ins."""

    return ScenarioRunner(
        load_builtin_scenarios(),
        workspace=workspace,
        evidence_root=evidence_root,
        approval_provider=approval_provider,
    )


def export_evidence(
    scenario_id: str,
    execution: ScenarioExecution,
    *,
    root: str | Path = DEFAULT_EVIDENCE_ROOT,
) -> RunResult:
    """Functional convenience wrapper around :class:`EvidenceExporter`."""

    return EvidenceExporter(root).export(scenario_id, execution)


__all__ = [
    "DEFAULT_EVIDENCE_ROOT",
    "EVIDENCE_SCHEMA_VERSION",
    "EvidenceExportError",
    "EvidenceExporter",
    "ScenarioContext",
    "ScenarioError",
    "ScenarioExecution",
    "ScenarioRunner",
    "ScenarioSpec",
    "build_run_result",
    "default_runner",
    "export_evidence",
    "load_builtin_scenarios",
]
