"""Append-only orchestration event storage.

All audit, lineage, and metrics views are projections over this stream.  The
store assigns and validates a monotonic sequence independently for each run and
can durably append the same immutable events to JSONL.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from orchestrator.contracts import Actor, Event, EventType, SYSTEM_ACTOR


class EventStoreError(RuntimeError):
    """Raised when persisted history violates append-only stream invariants."""


class EventStore:
    """In-memory event stream with optional append-only JSONL persistence.

    Passing ``path`` rehydrates an existing stream and verifies it before any
    new append.  Existing bytes are never rewritten by this class.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._events: list[Event] = []
        self._next_seq: dict[str, int] = {}
        self._lock = threading.RLock()
        if self._path is not None and self._path.exists():
            self._load()

    @property
    def path(self) -> Path | None:
        return self._path

    def append(
        self,
        *,
        run_id: str,
        type: EventType,
        at: str,
        actor: Actor = SYSTEM_ACTOR,
        task_id: str | None = None,
        summary: str = "",
        payload: dict[str, Any] | None = None,
    ) -> Event:
        """Create and append the next event for ``run_id`` atomically."""
        with self._lock:
            event = Event(
                run_id=run_id,
                seq=self._next_seq.get(run_id, 0),
                type=type,
                at=at,
                actor=actor,
                task_id=task_id,
                summary=summary,
                payload=dict(payload or {}),
            )
            self._append_validated(event)
            return event

    def append_event(self, event: Event) -> Event:
        """Append a pre-built event only if its sequence is exactly next.

        This method is useful when importing deterministic fixtures.  Normal
        engine code should call :meth:`append` and let the store assign ``seq``.
        """
        with self._lock:
            self._append_validated(event)
            return event

    def events(self, run_id: str | None = None) -> tuple[Event, ...]:
        """Return an immutable snapshot, optionally restricted to one run."""
        with self._lock:
            if run_id is None:
                selected = self._events
            else:
                selected = [event for event in self._events if event.run_id == run_id]
            # Event is frozen, but its payload is a mutable dict in the frozen
            # contract.  Deep copies prevent callers from rewriting history by
            # mutating a nested payload value.
            return tuple(event.model_copy(deep=True) for event in selected)

    def __iter__(self) -> Iterable[Event]:
        return iter(self.events())

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def _append_validated(self, event: Event, *, persist: bool = True) -> None:
        expected = self._next_seq.get(event.run_id, 0)
        if event.seq != expected:
            raise EventStoreError(
                f"run '{event.run_id}' expected event seq {expected}, got {event.seq}"
            )

        if persist and self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            serialized = event.model_dump_json() + "\n"
            with self._path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized)
                stream.flush()

        self._events.append(event.model_copy(deep=True))
        self._next_seq[event.run_id] = expected + 1

    def _load(self) -> None:
        assert self._path is not None
        try:
            with self._path.open("r", encoding="utf-8") as stream:
                for line_number, raw_line in enumerate(stream, start=1):
                    if not raw_line.strip():
                        raise EventStoreError(
                            f"blank record at {self._path}:{line_number}"
                        )
                    try:
                        event = Event.model_validate_json(raw_line)
                    except (ValueError, json.JSONDecodeError) as exc:
                        raise EventStoreError(
                            f"invalid event at {self._path}:{line_number}: {exc}"
                        ) from exc
                    self._append_validated(event, persist=False)
        except (TypeError, ValueError) as exc:
            raise EventStoreError(
                f"invalid event history in '{self._path}': {exc}"
            ) from exc
        except OSError as exc:
            raise EventStoreError(f"could not read event store '{self._path}': {exc}") from exc


JsonlEventStore = EventStore
