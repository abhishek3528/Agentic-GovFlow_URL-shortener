from __future__ import annotations

import sqlite3

import pytest

from app.codes import Sha256CodeGenerator
from app.repository import CollisionLimitExceeded, SqliteLinkRepository
from app.service import ShortenerService
from orchestrator.clock import FixedClock


class FirstCandidateCollision:
    def candidate(self, destination: str, collision_index: int) -> str:
        if collision_index == 0:
            return "sharedcode"
        return Sha256CodeGenerator().candidate(destination, collision_index)


class AlwaysCollides:
    def candidate(self, destination: str, collision_index: int) -> str:
        return "samecode"


def build_service(database_path, code_generator=None, max_collision_attempts=100):
    repository = SqliteLinkRepository(database_path, max_collision_attempts)
    repository.initialize()
    return ShortenerService(
        repository,
        code_generator or Sha256CodeGenerator(),
        FixedClock(),
    )


def test_same_destination_is_idempotent(tmp_path):
    service = build_service(tmp_path / "links.db")

    first = service.create("https://example.com/article")
    second = service.create("https://example.com/article")

    assert second == first


def test_collision_advances_to_a_deterministic_candidate(tmp_path):
    service = build_service(tmp_path / "links.db", FirstCandidateCollision())

    first = service.create("https://example.com/first")
    second = service.create("https://example.com/second")

    assert first.code == "sharedcode"
    assert second.code != first.code
    assert service.create("https://example.com/second").code == second.code


def test_collision_search_is_bounded(tmp_path):
    service = build_service(
        tmp_path / "links.db", AlwaysCollides(), max_collision_attempts=2
    )
    service.create("https://example.com/first")

    with pytest.raises(CollisionLimitExceeded):
        service.create("https://example.com/second")


def test_links_survive_repository_restart(tmp_path):
    database_path = tmp_path / "links.db"
    first_service = build_service(database_path)
    created = first_service.create("https://example.com/persistent")

    restarted_service = build_service(database_path)

    assert restarted_service.create("https://example.com/persistent") == created
    assert restarted_service.repository.get(created.code) == created


def test_click_storage_contains_no_raw_client_identifier_columns(tmp_path):
    database_path = tmp_path / "links.db"
    build_service(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(clicks)")}

    assert columns == {"id", "code", "clicked_at"}


def test_stats_include_coarse_daily_aggregates_without_client_identity(tmp_path):
    service = build_service(tmp_path / "links.db")
    link = service.create("https://example.com/daily")
    service.repository.record_click(link.code, "2026-01-01T23:59:59+00:00")
    service.repository.record_click(link.code, "2026-01-02T00:00:00+00:00")
    service.repository.record_click(link.code, "2026-01-02T12:00:00+00:00")

    stats = service.stats(link.code)

    assert stats is not None
    assert [(item.day, item.click_count) for item in stats.daily_clicks] == [
        ("2026-01-02", 2),
        ("2026-01-01", 1),
    ]
