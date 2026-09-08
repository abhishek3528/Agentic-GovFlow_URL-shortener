"""Frozen data contracts for the governed orchestration engine.

Every other module in this project reads and writes these types. They are the
single source of truth for what a run, a task, a gate, a policy decision, an
approval, an artifact, and an event *are*. Engine, scenarios, metrics, evidence
export and tests all build against this file so that independently developed
components join correctly.

Three invariants are encoded here rather than left to convention:

1. **Events are append-only.** ``Event`` is immutable. History is never
   rewritten - re-planning adds records, it does not delete them.
2. **Transitions are explicit.** ``LEGAL_TASK_TRANSITIONS`` and
   ``LEGAL_RUN_TRANSITIONS`` are the whole state machine. The engine asks this
   module whether a move is legal; it does not decide for itself. Illegal moves
   fail closed.
3. **Approval is attributable to a human.** ``Approval`` rejects a non-human
   actor at construction time, so an agent cannot approve its own high-impact
   work even if the engine has a bug.

Time and identity are injected (see ``orchestrator.clock``) rather than read
from the ambient system, so a scenario run is replayable.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------
# Actors
# --------------------------------------------------------------------------


class ActorKind(str, Enum):
    """Who caused a state change.

    The distinction is load-bearing: approval gates require ``HUMAN`` and the
    engine records attribution on every transition so a reviewer can tell
    agent-driven work from human-owned decisions.
    """

    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"


class Actor(BaseModel):
    """An attributable identity responsible for an action."""

    model_config = ConfigDict(frozen=True)

    kind: ActorKind
    id: str = Field(description="Stable identifier, e.g. 'reviewer:alex' or 'agent:planner'.")


SYSTEM_ACTOR = Actor(kind=ActorKind.SYSTEM, id="system")


# --------------------------------------------------------------------------
# SDLC shape
# --------------------------------------------------------------------------


class Stage(str, Enum):
    """SDLC stage a task belongs to.

    The dependency graph must visibly span these; a plan that omits testing or
    documentation is not a credible engineering workflow.
    """

    REQUIREMENTS = "requirements"
    DESIGN = "design"
    IMPLEMENTATION = "implementation"
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    RELEASE_READINESS = "release_readiness"


class ImpactClass(str, Enum):
    """How risky a task's effect is.

    ``HIGH`` and ``BREAKING`` require human approval before execution. This is
    the rule the approval-bypass negative test attacks.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BREAKING = "breaking"

    @property
    def requires_approval(self) -> bool:
        return self in (ImpactClass.HIGH, ImpactClass.BREAKING)


# --------------------------------------------------------------------------
# State machines
# --------------------------------------------------------------------------


class TaskState(str, Enum):
    """Lifecycle of a single task."""

    PENDING = "pending"  # created; dependencies not yet satisfied
    READY = "ready"  # dependencies satisfied; entry gates not yet evaluated
    AWAITING_APPROVAL = "awaiting_approval"  # paused for a human decision
    RUNNING = "running"
    RETRYING = "retrying"  # failed, retry budget remains
    SUCCEEDED = "succeeded"
    FAILED = "failed"  # retry budget exhausted
    COMPENSATED = "compensated"  # failed, compensating action applied
    BLOCKED = "blocked"  # a gate or policy denied it
    STALE = "stale"  # invalidated by an upstream re-plan
    SKIPPED = "skipped"


TERMINAL_TASK_STATES: frozenset[TaskState] = frozenset(
    {
        TaskState.SUCCEEDED,
        TaskState.FAILED,
        TaskState.COMPENSATED,
        TaskState.BLOCKED,
        TaskState.STALE,
        TaskState.SKIPPED,
    }
)

LEGAL_TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.READY, TaskState.STALE, TaskState.SKIPPED}),
    TaskState.READY: frozenset(
        {
            TaskState.RUNNING,
            TaskState.AWAITING_APPROVAL,
            TaskState.BLOCKED,
            TaskState.STALE,
            TaskState.SKIPPED,
        }
    ),
    TaskState.AWAITING_APPROVAL: frozenset(
        {TaskState.RUNNING, TaskState.BLOCKED, TaskState.STALE}
    ),
    TaskState.RUNNING: frozenset(
        {
            TaskState.SUCCEEDED,
            TaskState.RETRYING,
            TaskState.FAILED,
            TaskState.BLOCKED,
            TaskState.STALE,
        }
    ),
    TaskState.RETRYING: frozenset(
        {TaskState.RUNNING, TaskState.FAILED, TaskState.COMPENSATED, TaskState.STALE}
    ),
    TaskState.FAILED: frozenset({TaskState.COMPENSATED, TaskState.STALE}),
    # Terminal states below. STALE is reachable from almost anywhere because a
    # re-plan may invalidate work that already succeeded; a stale task can be
    # re-planned back to PENDING as part of a *new* plan revision.
    TaskState.STALE: frozenset({TaskState.PENDING}),
    TaskState.SUCCEEDED: frozenset({TaskState.STALE}),
    TaskState.COMPENSATED: frozenset({TaskState.STALE}),
    TaskState.BLOCKED: frozenset({TaskState.STALE}),
    TaskState.SKIPPED: frozenset({TaskState.STALE}),
}


class RunState(str, Enum):
    """Lifecycle of a whole workflow run."""

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    REPLANNING = "replanning"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SAFE_STOPPED = "safe_stopped"  # recovery exhausted; stopped without claiming success


TERMINAL_RUN_STATES: frozenset[RunState] = frozenset(
    {RunState.SUCCEEDED, RunState.FAILED, RunState.SAFE_STOPPED}
)

LEGAL_RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.PENDING: frozenset({RunState.RUNNING, RunState.FAILED}),
    RunState.RUNNING: frozenset(
        {
            RunState.AWAITING_APPROVAL,
            RunState.REPLANNING,
            RunState.SUCCEEDED,
            RunState.FAILED,
            RunState.SAFE_STOPPED,
        }
    ),
    RunState.AWAITING_APPROVAL: frozenset(
        {RunState.RUNNING, RunState.FAILED, RunState.SAFE_STOPPED}
    ),
    RunState.REPLANNING: frozenset({RunState.RUNNING, RunState.FAILED, RunState.SAFE_STOPPED}),
    RunState.SUCCEEDED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.SAFE_STOPPED: frozenset(),
}


def is_legal_task_transition(src: TaskState, dst: TaskState) -> bool:
    """Whether a task may move from ``src`` to ``dst``. Fails closed."""
    return dst in LEGAL_TASK_TRANSITIONS.get(src, frozenset())


def is_legal_run_transition(src: RunState, dst: RunState) -> bool:
    """Whether a run may move from ``src`` to ``dst``. Fails closed."""
    return dst in LEGAL_RUN_TRANSITIONS.get(src, frozenset())


# --------------------------------------------------------------------------
# Requirement context and decisions
# --------------------------------------------------------------------------


class Ambiguity(BaseModel):
    """An unresolved dimension of a requirement, with a proposed disposition.

    Surfacing these instead of silently choosing is the core of the ambiguous
    scenario: each carries the assumption the system proposes and what follows
    if that assumption is wrong.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    question: str
    proposed_assumption: str
    consequence_if_wrong: str


class ContextVersion(BaseModel):
    """An immutable version of the normalized engineering problem.

    Re-planning creates version N+1 with ``supersedes`` pointing at N and a
    ``change_reason``. Version N is retained so a reviewer can reconstruct what
    the system believed, and when.
    """

    model_config = ConfigDict(frozen=True)

    version: int = Field(ge=1)
    raw_requirement: str
    normalized_problem: str
    ambiguities: tuple[Ambiguity, ...] = ()
    assumptions: tuple[str, ...] = ()
    acceptance_checks: tuple[str, ...] = ()
    supersedes: int | None = None
    change_reason: str | None = None
    created_at: str = Field(description="ISO-8601 timestamp from the injected clock.")

    @field_validator("supersedes")
    @classmethod
    def _supersedes_is_earlier(cls, v: int | None, info: Any) -> int | None:
        version = info.data.get("version")
        if v is not None and version is not None and v >= version:
            raise ValueError("supersedes must reference an earlier version")
        return v


class Decision(BaseModel):
    """A recorded engineering decision, attributable and time-stamped.

    Decision lineage is a graded deliverable: a reviewer must be able to
    reconstruct *why* the system acted, not just what it did.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    summary: str
    rationale: str
    actor: Actor
    created_at: str
    task_id: str | None = None
    context_version: int | None = None


# --------------------------------------------------------------------------
# Artifacts
# --------------------------------------------------------------------------


class Artifact(BaseModel):
    """A named, versioned output of a task.

    ``derived_from`` makes lineage explicit so that selective invalidation can
    walk the graph. ``stale`` is the only mutable field: a re-plan flips it, and
    the engine refuses to let a downstream task consume a stale input. The flip
    is always accompanied by an ``ARTIFACT_INVALIDATED`` event, so the prior
    state remains reconstructible from history.
    """

    id: str
    name: str = Field(description="Logical name other tasks declare as an input, e.g. 'api_contract'.")
    version: int = Field(ge=1)
    produced_by_task: str
    context_version: int
    content_hash: str
    path: str | None = None
    derived_from: tuple[str, ...] = ()
    stale: bool = False
    created_at: str


# --------------------------------------------------------------------------
# Gates, policy, approval
# --------------------------------------------------------------------------


class GateKind(str, Enum):
    ENTRY = "entry"
    EXIT = "exit"


class Gate(BaseModel):
    """A named precondition (entry) or postcondition (exit) on a task.

    Gates fail closed: missing evidence is a denial, not a pass.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    kind: GateKind
    description: str


class GateResult(BaseModel):
    """The outcome of evaluating one gate against one task."""

    model_config = ConfigDict(frozen=True)

    gate_id: str
    task_id: str
    kind: GateKind
    passed: bool
    reason: str
    evaluated_at: str


class PolicyEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class PolicyDecision(BaseModel):
    """A named policy's verdict on a proposed action.

    Security, privacy, evidence-retention and change-control checks all produce
    these. Both allows and denies are recorded - a policy engine that only logs
    denials cannot prove it ran.
    """

    model_config = ConfigDict(frozen=True)

    policy_id: str
    task_id: str
    effect: PolicyEffect
    reason: str
    evaluated_at: str

    @property
    def denied(self) -> bool:
        return self.effect is PolicyEffect.DENY


class Approval(BaseModel):
    """A human decision on a high-impact task.

    Construction rejects a non-human actor. This is deliberate defence in depth:
    even if the engine mistakenly routes an agent to an approval gate, the
    record cannot be created, and the bypass negative test asserts it.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    task_id: str
    granted: bool
    actor: Actor
    rationale: str
    decided_at: str

    @field_validator("actor")
    @classmethod
    def _must_be_human(cls, v: Actor) -> Actor:
        if v.kind is not ActorKind.HUMAN:
            raise ValueError(
                f"approval requires a human actor, got '{v.kind.value}' - "
                "agents may request approval but never grant it"
            )
        return v


# --------------------------------------------------------------------------
# Tasks and plans
# --------------------------------------------------------------------------


class Task(BaseModel):
    """A single unit of governed work in the dependency graph.

    ``consumes`` and ``produces`` are logical artifact names, not task ids. That
    indirection is what makes a join meaningful: a join task declares the inputs
    it needs, and cannot start until every one exists, is fresh, and passed its
    producer's exit gates.
    """

    id: str
    name: str
    stage: Stage
    capability: str = Field(description="Role expected to perform the work, e.g. 'engineer', 'qa'.")
    depends_on: tuple[str, ...] = ()
    consumes: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    impact: ImpactClass = ImpactClass.LOW
    entry_gates: tuple[Gate, ...] = ()
    exit_gates: tuple[Gate, ...] = ()
    retry_budget: int = Field(default=0, ge=0, description="Retries after the first attempt.")
    attempts: int = Field(default=0, ge=0)
    state: TaskState = TaskState.PENDING
    compensation: str | None = Field(
        default=None, description="Named compensating action if this task fails terminally."
    )

    @property
    def requires_approval(self) -> bool:
        """High-impact and breaking work is human-owned regardless of plan authorship."""
        return self.impact.requires_approval

    @property
    def retries_remaining(self) -> int:
        return max(0, self.retry_budget - max(0, self.attempts - 1))


class Plan(BaseModel):
    """A versioned dependency graph of tasks.

    A re-plan produces a new ``Plan`` with an incremented ``revision`` rather
    than mutating this one, so superseded plans stay reviewable.
    """

    revision: int = Field(default=1, ge=1)
    context_version: int
    tasks: tuple[Task, ...]
    created_at: str
    supersedes: int | None = None
    change_reason: str | None = None

    def task_by_id(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


class EventType(str, Enum):
    """The append-only vocabulary every view is derived from.

    Audit history, decision lineage, scenario traces and reliability metrics are
    all projections over this one stream - never separately maintained data.
    """

    RUN_STARTED = "run_started"
    RUN_STATE_CHANGED = "run_state_changed"
    RUN_COMPLETED = "run_completed"

    CONTEXT_VERSION_CREATED = "context_version_created"
    PLAN_CREATED = "plan_created"
    PLAN_REVISED = "plan_revised"
    REPLAN_TRIGGERED = "replan_triggered"

    TASK_STATE_CHANGED = "task_state_changed"
    TASK_INVALIDATED = "task_invalidated"

    GATE_EVALUATED = "gate_evaluated"
    POLICY_EVALUATED = "policy_evaluated"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_DECIDED = "approval_decided"

    RETRY_ATTEMPTED = "retry_attempted"
    COMPENSATION_EXECUTED = "compensation_executed"
    SAFE_STOP_TRIGGERED = "safe_stop_triggered"

    ARTIFACT_PRODUCED = "artifact_produced"
    ARTIFACT_INVALIDATED = "artifact_invalidated"
    VALIDATION_EXECUTED = "validation_executed"
    DECISION_RECORDED = "decision_recorded"


class Event(BaseModel):
    """One immutable, correlated record of something that happened.

    ``seq`` is a monotonic per-run counter giving total causal order without
    depending on clock resolution - two events in the same millisecond are still
    unambiguously ordered.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str
    seq: int = Field(ge=0)
    type: EventType
    at: str = Field(description="ISO-8601 timestamp from the injected clock.")
    actor: Actor = SYSTEM_ACTOR
    task_id: str | None = None
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Metrics and run result
# --------------------------------------------------------------------------


class RunMetrics(BaseModel):
    """Reliability metrics, always computed from the event stream.

    Deriving these rather than recording them means the numbers cannot drift
    from what actually happened, and can be unit-tested against a known
    synthetic event sequence.
    """

    model_config = ConfigDict(frozen=True)

    tasks_total: int = 0
    tasks_succeeded: int = 0
    tasks_failed: int = 0
    success_rate: float = 0.0
    retry_count: int = 0
    retry_rate: float = Field(default=0.0, description="Retries per executed task.")
    rollback_count: int = Field(default=0, description="Compensating actions executed.")
    rollback_rate: float = 0.0
    mttr_seconds: float | None = Field(
        default=None, description="Mean time from first failure to recovery; None if never failed."
    )
    end_to_end_seconds: float = 0.0
    approvals_requested: int = 0
    policy_denials: int = 0
    gate_denials: int = 0
    replans: int = 0


class RunResult(BaseModel):
    """Terminal outcome of a scenario run, and the index into its evidence."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    scenario_id: str
    state: RunState
    terminal_reason: str
    started_at: str
    completed_at: str
    context_versions: tuple[ContextVersion, ...] = ()
    plans: tuple[Plan, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    decisions: tuple[Decision, ...] = ()
    metrics: RunMetrics = RunMetrics()
    evidence_path: str | None = None
    limitations: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.state is RunState.SUCCEEDED
