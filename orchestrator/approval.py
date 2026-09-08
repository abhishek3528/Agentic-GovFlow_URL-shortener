"""Human-owned approval records for high-impact orchestration tasks.

The controller tracks request/decision correlation only.  It never moves task
or run state and never writes events; those remain engine responsibilities.
"""

from __future__ import annotations

from orchestrator.contracts import Approval


class ApprovalError(RuntimeError):
    """Raised when an approval is missing, duplicated, or mis-correlated."""


class ApprovalController:
    """In-memory request registry with immutable human decisions."""

    def __init__(self) -> None:
        self._pending: set[str] = set()
        self._decisions: list[Approval] = []

    def request(self, task_id: str) -> None:
        if task_id in self._pending:
            raise ApprovalError(f"task '{task_id}' already has a pending approval request")
        # A later plan revision may invalidate a previously approved task.  A
        # new request is then required and the earlier decision is retained.
        self._pending.add(task_id)

    def decide(self, approval: Approval) -> Approval:
        # Approval's frozen contract independently rejects every non-human
        # actor.  This layer adds request correlation and one-decision-only.
        if approval.task_id not in self._pending:
            raise ApprovalError(
                f"task '{approval.task_id}' has no pending approval request"
            )
        self._pending.remove(approval.task_id)
        self._decisions.append(approval)
        return approval

    def cancel_request(self, task_id: str) -> None:
        """Cancel a pending request when its plan revision is invalidated."""
        self._pending.discard(task_id)

    def decision_for(self, task_id: str) -> Approval | None:
        return next(
            (decision for decision in reversed(self._decisions) if decision.task_id == task_id),
            None,
        )

    def is_pending(self, task_id: str) -> bool:
        return task_id in self._pending

    @property
    def decisions(self) -> tuple[Approval, ...]:
        return tuple(self._decisions)


ApprovalManager = ApprovalController
