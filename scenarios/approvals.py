"""Approval decision providers for deterministic and human-driven scenarios."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Protocol, TextIO

from orchestrator.contracts import Actor, ActorKind, Approval


class ApprovalProvider(Protocol):
    """Supply the human decision for one scenario approval checkpoint."""

    def decide(
        self,
        *,
        task_id: str,
        impact: str,
        summary: str,
        default_actor: Actor,
        default_rationale: str,
        approval_id: str,
        decided_at: str,
    ) -> Approval:
        """Return an attributable decision for the requested task."""


@dataclass(frozen=True)
class ScriptedApprovalProvider:
    """Replay the deterministic human-attributed fixtures used by scenarios."""

    def decide(
        self,
        *,
        task_id: str,
        impact: str,
        summary: str,
        default_actor: Actor,
        default_rationale: str,
        approval_id: str,
        decided_at: str,
    ) -> Approval:
        del impact, summary
        return Approval(
            id=approval_id,
            task_id=task_id,
            granted=True,
            actor=default_actor,
            rationale=default_rationale,
            decided_at=decided_at,
        )


class InteractiveApprovalError(RuntimeError):
    """An interactive decision could not be obtained safely."""


@dataclass
class InteractiveApprovalProvider:
    """Pause at approval gates and collect an attributable terminal decision."""

    input_stream: TextIO = field(default_factory=lambda: sys.stdin)
    output_stream: TextIO = field(default_factory=lambda: sys.stdout)

    def decide(
        self,
        *,
        task_id: str,
        impact: str,
        summary: str,
        default_actor: Actor,
        default_rationale: str,
        approval_id: str,
        decided_at: str,
    ) -> Approval:
        del default_actor, default_rationale
        if not self.input_stream.isatty():
            raise InteractiveApprovalError(
                "interactive approvals require a TTY on standard input"
            )

        self._write("\nHuman approval required")
        self._write(f"  Task: {task_id}")
        self._write(f"  Impact: {impact}")
        self._write(f"  Approving: {summary}")
        name = self._required("Approver name: ")
        rationale = self._required("Rationale: ")
        granted = self._yes_or_no("Grant approval? [y/n]: ")
        return Approval(
            id=approval_id,
            task_id=task_id,
            granted=granted,
            actor=Actor(kind=ActorKind.HUMAN, id=f"reviewer:{name}"),
            rationale=rationale,
            decided_at=decided_at,
        )

    def _required(self, prompt: str) -> str:
        while True:
            value = self._read(prompt).strip()
            if value:
                return value
            self._write("A non-empty value is required.")

    def _yes_or_no(self, prompt: str) -> bool:
        while True:
            answer = self._read(prompt).strip().lower()
            if answer in {"y", "yes"}:
                return True
            if answer in {"n", "no"}:
                return False
            self._write("Enter 'y' or 'n'.")

    def _read(self, prompt: str) -> str:
        self.output_stream.write(prompt)
        self.output_stream.flush()
        value = self.input_stream.readline()
        if value == "":
            raise InteractiveApprovalError(
                "interactive approval input ended before a decision was provided"
            )
        return value

    def _write(self, value: str) -> None:
        self.output_stream.write(value + "\n")
        self.output_stream.flush()


__all__ = [
    "ApprovalProvider",
    "InteractiveApprovalError",
    "InteractiveApprovalProvider",
    "ScriptedApprovalProvider",
]
