"""Deterministic, named policy guardrails for governed task execution.

Policies return the frozen :class:`PolicyDecision` contract for both allows and
denials.  They do not mutate workflow state or write events; the orchestration
engine owns those responsibilities and records the returned decisions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol
from urllib.parse import urlsplit

from orchestrator.clock import Clock, FixedClock
from orchestrator.contracts import ImpactClass, PolicyDecision, PolicyEffect


URL_SAFETY_POLICY = "url-safety"
PRIVACY_POLICY = "privacy"
CHANGE_CONTROL_POLICY = "change-control"
EVIDENCE_RETENTION_POLICY = "evidence-retention"


class NamedPolicy(Protocol):
    """A policy that evaluates task-scoped, serializable context."""

    policy_id: str

    def evaluate(
        self,
        task_id: str,
        context: Mapping[str, object],
        *,
        evaluated_at: str,
    ) -> PolicyDecision: ...


def _decision(
    policy_id: str,
    task_id: str,
    allowed: bool,
    reason: str,
    evaluated_at: str,
) -> PolicyDecision:
    return PolicyDecision(
        policy_id=policy_id,
        task_id=task_id,
        effect=PolicyEffect.ALLOW if allowed else PolicyEffect.DENY,
        reason=reason,
        evaluated_at=evaluated_at,
    )


class URLSafetyPolicy:
    """Allow only absolute HTTP(S) destinations and reject active schemes."""

    policy_id = URL_SAFETY_POLICY

    def __init__(self, allowed_schemes: Iterable[str] = ("http", "https")) -> None:
        normalized = frozenset(str(scheme).strip().lower() for scheme in allowed_schemes)
        # These schemes remain forbidden even if a caller accidentally puts one
        # in a configured allowlist.
        self._allowed_schemes = normalized - {"javascript", "data"}

    def evaluate(
        self,
        task_id: str,
        context: Mapping[str, object],
        *,
        evaluated_at: str,
    ) -> PolicyDecision:
        destination = context.get("destination")
        if not isinstance(destination, str) or not destination.strip():
            return _decision(
                self.policy_id,
                task_id,
                False,
                "destination is missing; URL safety failed closed",
                evaluated_at,
            )

        try:
            parsed = urlsplit(destination.strip())
            scheme = parsed.scheme.lower()
            hostname = parsed.hostname
        except ValueError as exc:
            return _decision(
                self.policy_id,
                task_id,
                False,
                f"destination is malformed: {exc}",
                evaluated_at,
            )

        if scheme in {"javascript", "data"}:
            return _decision(
                self.policy_id,
                task_id,
                False,
                f"'{scheme}:' destinations are explicitly prohibited",
                evaluated_at,
            )
        if scheme not in self._allowed_schemes:
            shown = scheme or "<missing>"
            return _decision(
                self.policy_id,
                task_id,
                False,
                f"scheme '{shown}' is not in the URL scheme allowlist",
                evaluated_at,
            )
        if hostname is None:
            return _decision(
                self.policy_id,
                task_id,
                False,
                "destination must be absolute and include a host",
                evaluated_at,
            )
        if parsed.username is not None or parsed.password is not None:
            return _decision(
                self.policy_id,
                task_id,
                False,
                "embedded URL credentials are prohibited",
                evaluated_at,
            )
        return _decision(
            self.policy_id,
            task_id,
            True,
            f"absolute destination uses allowed scheme '{scheme}'",
            evaluated_at,
        )


class PrivacyPolicy:
    """Deny retention of raw request or client-identifying fields."""

    policy_id = PRIVACY_POLICY
    _prohibited = frozenset(
        {
            "ip",
            "ip_address",
            "client_ip",
            "remote_addr",
            "user_agent",
            "device_id",
            "advertising_id",
            "cookie",
            "session_id",
            "client_id",
            "client_identifier",
            "raw_client_identifier",
            "raw_request_headers",
        }
    )

    @staticmethod
    def _normalize(field: str) -> str:
        return field.strip().lower().replace("-", "_").replace(" ", "_")

    def evaluate(
        self,
        task_id: str,
        context: Mapping[str, object],
        *,
        evaluated_at: str,
    ) -> PolicyDecision:
        explicit = context.get("raw_client_identifiers_retained")
        if explicit is True:
            return _decision(
                self.policy_id,
                task_id,
                False,
                "raw client identifiers would be retained",
                evaluated_at,
            )
        if explicit is not None and not isinstance(explicit, bool):
            return _decision(
                self.policy_id,
                task_id,
                False,
                "raw-client-identifier retention flag is invalid; privacy failed closed",
                evaluated_at,
            )

        raw_fields = context.get("retained_fields", ())
        if isinstance(raw_fields, str) or not isinstance(raw_fields, Iterable):
            return _decision(
                self.policy_id,
                task_id,
                False,
                "retained_fields must be an iterable of field names",
                evaluated_at,
            )

        normalized: set[str] = set()
        for field in raw_fields:
            if not isinstance(field, str):
                return _decision(
                    self.policy_id,
                    task_id,
                    False,
                    "retained_fields contains a non-string value; privacy failed closed",
                    evaluated_at,
                )
            normalized.add(self._normalize(field))

        prohibited = sorted(
            field
            for field in normalized
            if field in self._prohibited or field.startswith("raw_")
        )
        if prohibited:
            return _decision(
                self.policy_id,
                task_id,
                False,
                "raw client-identifying fields are prohibited: " + ", ".join(prohibited),
                evaluated_at,
            )
        return _decision(
            self.policy_id,
            task_id,
            True,
            "retained analytics fields contain no raw client identifiers",
            evaluated_at,
        )


class ChangeControlPolicy:
    """Require breaking changes to be classified as breaking impact."""

    policy_id = CHANGE_CONTROL_POLICY

    def evaluate(
        self,
        task_id: str,
        context: Mapping[str, object],
        *,
        evaluated_at: str,
    ) -> PolicyDecision:
        is_breaking = context.get("is_breaking")
        declared = context.get("declared_impact")
        if not isinstance(is_breaking, bool):
            return _decision(
                self.policy_id,
                task_id,
                False,
                "breaking-change analysis is missing; change control failed closed",
                evaluated_at,
            )
        try:
            impact = declared if isinstance(declared, ImpactClass) else ImpactClass(declared)
        except (TypeError, ValueError):
            return _decision(
                self.policy_id,
                task_id,
                False,
                "declared impact classification is missing or invalid",
                evaluated_at,
            )
        if is_breaking and impact is not ImpactClass.BREAKING:
            return _decision(
                self.policy_id,
                task_id,
                False,
                f"breaking change was under-classified as '{impact.value}'",
                evaluated_at,
            )
        return _decision(
            self.policy_id,
            task_id,
            True,
            (
                "breaking change is classified for human-owned approval"
                if is_breaking
                else f"non-breaking change is classified as '{impact.value}'"
            ),
            evaluated_at,
        )


class EvidenceRetentionPolicy:
    """Require append-only evidence retention for a bounded minimum period."""

    policy_id = EVIDENCE_RETENTION_POLICY

    def __init__(self, minimum_days: int = 30) -> None:
        if (
            isinstance(minimum_days, bool)
            or not isinstance(minimum_days, int)
            or minimum_days < 1
        ):
            raise ValueError("minimum_days must be a positive integer")
        self._minimum_days = minimum_days

    def evaluate(
        self,
        task_id: str,
        context: Mapping[str, object],
        *,
        evaluated_at: str,
    ) -> PolicyDecision:
        retention_days = context.get("retention_days")
        append_only = context.get("append_only")
        if isinstance(retention_days, bool) or not isinstance(retention_days, int):
            return _decision(
                self.policy_id,
                task_id,
                False,
                "evidence retention period is missing or invalid",
                evaluated_at,
            )
        if retention_days < self._minimum_days:
            return _decision(
                self.policy_id,
                task_id,
                False,
                f"evidence retention of {retention_days} days is below the "
                f"{self._minimum_days}-day policy minimum",
                evaluated_at,
            )
        if append_only is not True:
            return _decision(
                self.policy_id,
                task_id,
                False,
                "evidence storage is not declared append-only",
                evaluated_at,
            )
        return _decision(
            self.policy_id,
            task_id,
            True,
            f"append-only evidence is retained for {retention_days} days",
            evaluated_at,
        )


class PolicyEngine:
    """Registry and deterministic evaluation facade for named policies."""

    def __init__(
        self,
        policies: Iterable[NamedPolicy] | None = None,
        *,
        clock: Clock | None = None,
    ) -> None:
        defaults: tuple[NamedPolicy, ...] = (
            URLSafetyPolicy(),
            PrivacyPolicy(),
            ChangeControlPolicy(),
            EvidenceRetentionPolicy(),
        )
        selected = tuple(policies) if policies is not None else defaults
        self._policies = {policy.policy_id: policy for policy in selected}
        if len(self._policies) != len(selected):
            raise ValueError("policy ids must be unique")
        self._clock = clock or FixedClock()

    @property
    def policy_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._policies))

    def evaluate(
        self,
        policy_id: str,
        task_id: str,
        context: Mapping[str, object],
    ) -> PolicyDecision:
        policy = self._policies.get(policy_id)
        evaluated_at = self._clock.iso()
        if policy is None:
            return _decision(
                policy_id,
                task_id,
                False,
                "unknown policy; evaluation failed closed",
                evaluated_at,
            )
        try:
            return policy.evaluate(task_id, context, evaluated_at=evaluated_at)
        except Exception as exc:
            return _decision(
                policy_id,
                task_id,
                False,
                f"policy evaluation failed closed: {type(exc).__name__}: {exc}",
                evaluated_at,
            )

    def evaluate_url(self, task_id: str, destination: str) -> PolicyDecision:
        return self.evaluate(URL_SAFETY_POLICY, task_id, {"destination": destination})

    def evaluate_privacy(
        self,
        task_id: str,
        retained_fields: Iterable[str],
        *,
        raw_client_identifiers_retained: bool = False,
    ) -> PolicyDecision:
        return self.evaluate(
            PRIVACY_POLICY,
            task_id,
            {
                "retained_fields": tuple(retained_fields),
                "raw_client_identifiers_retained": raw_client_identifiers_retained,
            },
        )

    def evaluate_change(
        self,
        task_id: str,
        *,
        is_breaking: bool,
        declared_impact: ImpactClass,
    ) -> PolicyDecision:
        return self.evaluate(
            CHANGE_CONTROL_POLICY,
            task_id,
            {"is_breaking": is_breaking, "declared_impact": declared_impact},
        )

    def evaluate_evidence_retention(
        self,
        task_id: str,
        *,
        retention_days: int,
        append_only: bool,
    ) -> PolicyDecision:
        return self.evaluate(
            EVIDENCE_RETENTION_POLICY,
            task_id,
            {"retention_days": retention_days, "append_only": append_only},
        )


PolicyEvaluator = PolicyEngine
