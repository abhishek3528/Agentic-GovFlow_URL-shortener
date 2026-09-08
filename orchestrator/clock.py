"""Injected time and identity, so that scenario runs are replayable.

Nothing in the engine calls ``datetime.now()`` or ``uuid4()`` directly. Both
come from here, and both have a deterministic implementation used by scenarios
and tests. This is why an evidence bundle can be regenerated and diffed: run the
same scenario twice and the structure, ordering and ids are identical.

``SystemClock`` exists for the live HTTP service, where real wall-clock time is
the correct behaviour.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    """Source of timestamps."""

    def now(self) -> datetime: ...

    def iso(self) -> str:
        """Current time as an ISO-8601 string, the form stored in events."""
        ...


class IdGen(Protocol):
    """Source of identifiers."""

    def next_id(self, prefix: str) -> str: ...


class SystemClock:
    """Real wall-clock time. Used by the running service."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def iso(self) -> str:
        return self.now().isoformat()


class FixedClock:
    """Deterministic clock that advances by a fixed step on every read.

    A monotonic step (rather than a frozen instant) keeps duration-based metrics
    such as MTTR and end-to-end latency meaningful while staying reproducible:
    the same sequence of calls always yields the same timestamps.
    """

    def __init__(
        self,
        start: datetime | None = None,
        step: timedelta = timedelta(seconds=1),
    ) -> None:
        self._current = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
        self._step = step

    def now(self) -> datetime:
        value = self._current
        self._current = self._current + self._step
        return value

    def iso(self) -> str:
        return self.now().isoformat()

    def peek(self) -> datetime:
        """Read the next timestamp without consuming it (test convenience)."""
        return self._current


class SeededIdGen:
    """Deterministic, human-readable ids of the form ``<prefix>-0001``.

    Readability matters here: these ids appear throughout evidence bundles that
    a reviewer reads by hand, and ``task-0003`` traces far better than a UUID.
    """

    def __init__(self) -> None:
        self._counters: dict[str, itertools.count] = {}

    def next_id(self, prefix: str) -> str:
        counter = self._counters.setdefault(prefix, itertools.count(1))
        return f"{prefix}-{next(counter):04d}"


class RandomIdGen:
    """Non-deterministic ids for the live service, where collisions matter more
    than reproducibility."""

    def next_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:12]}"


def deterministic_pair() -> tuple[FixedClock, SeededIdGen]:
    """The standard (clock, id generator) pair for scenarios and tests."""
    return FixedClock(), SeededIdGen()
