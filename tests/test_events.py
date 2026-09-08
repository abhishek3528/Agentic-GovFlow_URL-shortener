from __future__ import annotations

import pytest

from orchestrator.contracts import Event, EventType
from orchestrator.events import EventStore, EventStoreError


def test_store_assigns_monotonic_sequence_per_run():
    store = EventStore()
    first = store.append(
        run_id="run-a",
        type=EventType.RUN_STARTED,
        at="2026-01-01T00:00:00+00:00",
    )
    other = store.append(
        run_id="run-b",
        type=EventType.RUN_STARTED,
        at="2026-01-01T00:00:01+00:00",
    )
    second = store.append(
        run_id="run-a",
        type=EventType.PLAN_CREATED,
        at="2026-01-01T00:00:02+00:00",
    )

    assert (first.seq, second.seq, other.seq) == (0, 1, 0)
    assert store.events("run-a") == (first, second)


def test_jsonl_store_rehydrates_without_rewriting(tmp_path):
    path = tmp_path / "events.jsonl"
    store = EventStore(path)
    store.append(
        run_id="run-1",
        type=EventType.RUN_STARTED,
        at="2026-01-01T00:00:00+00:00",
    )
    original = path.read_bytes()

    restored = EventStore(path)
    restored.append(
        run_id="run-1",
        type=EventType.PLAN_CREATED,
        at="2026-01-01T00:00:01+00:00",
    )

    assert path.read_bytes().startswith(original)
    assert [event.seq for event in restored.events("run-1")] == [0, 1]


def test_prebuilt_event_cannot_skip_or_reuse_sequence():
    store = EventStore()
    event = Event(
        run_id="run-1",
        seq=1,
        type=EventType.RUN_STARTED,
        at="2026-01-01T00:00:00+00:00",
    )
    with pytest.raises(EventStoreError, match="expected event seq 0"):
        store.append_event(event)


def test_nested_payload_mutation_cannot_rewrite_stored_history():
    store = EventStore()
    event = store.append(
        run_id="run-1",
        type=EventType.PLAN_CREATED,
        at="2026-01-01T00:00:00+00:00",
        payload={"task_ids": ["a"]},
    )
    event.payload["task_ids"].append("forged")
    snapshot = store.events("run-1")[0]
    snapshot.payload["task_ids"].append("also-forged")

    assert store.events("run-1")[0].payload == {"task_ids": ["a"]}
