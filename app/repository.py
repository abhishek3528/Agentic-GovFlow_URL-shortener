"""SQLite persistence for links and privacy-conscious click analytics."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class LinkRecord:
    code: str
    destination: str
    created_at: str


@dataclass(frozen=True)
class LinkStatsRecord:
    link: LinkRecord
    click_count: int
    recent_clicks: tuple[str, ...]
    daily_clicks: tuple["DailyClickCount", ...] = ()


@dataclass(frozen=True)
class DailyClickCount:
    """A coarse UTC-day aggregate derived without retaining client identity."""

    day: str
    click_count: int


class CollisionLimitExceeded(RuntimeError):
    """No free deterministic candidate was found within the configured bound."""


class SqliteLinkRepository:
    def __init__(self, database_path: str | Path, max_collision_attempts: int = 100) -> None:
        if max_collision_attempts < 1:
            raise ValueError("max_collision_attempts must be positive")
        self.database_path = Path(database_path)
        self.max_collision_attempts = max_collision_attempts

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS links (
                    code TEXT PRIMARY KEY,
                    destination TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS clicks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL REFERENCES links(code) ON DELETE CASCADE,
                    clicked_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_clicks_code_id
                    ON clicks(code, id DESC);
                """
            )

    def ready(self) -> bool:
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def create_or_get(
        self,
        destination: str,
        created_at: str,
        candidate_for: Callable[[int], str],
    ) -> LinkRecord:
        """Atomically return an existing link or insert a collision-free one."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT code, destination, created_at FROM links WHERE destination = ?",
                (destination,),
            ).fetchone()
            if existing is not None:
                return self._link(existing)

            for collision_index in range(self.max_collision_attempts):
                code = candidate_for(collision_index)
                collision = connection.execute(
                    "SELECT destination FROM links WHERE code = ?", (code,)
                ).fetchone()
                if collision is not None:
                    continue

                connection.execute(
                    "INSERT INTO links(code, destination, created_at) VALUES (?, ?, ?)",
                    (code, destination, created_at),
                )
                return LinkRecord(code=code, destination=destination, created_at=created_at)

        raise CollisionLimitExceeded(
            f"no free code after {self.max_collision_attempts} deterministic candidates"
        )

    def get(self, code: str) -> LinkRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT code, destination, created_at FROM links WHERE code = ?", (code,)
            ).fetchone()
        return None if row is None else self._link(row)

    def record_click(self, code: str, clicked_at: str) -> bool:
        with self._connect() as connection:
            exists = connection.execute("SELECT 1 FROM links WHERE code = ?", (code,)).fetchone()
            if exists is None:
                return False
            connection.execute(
                "INSERT INTO clicks(code, clicked_at) VALUES (?, ?)", (code, clicked_at)
            )
        return True

    def stats(self, code: str, recent_limit: int = 20) -> LinkStatsRecord | None:
        with self._connect() as connection:
            link_row = connection.execute(
                "SELECT code, destination, created_at FROM links WHERE code = ?", (code,)
            ).fetchone()
            if link_row is None:
                return None
            count = connection.execute(
                "SELECT COUNT(*) FROM clicks WHERE code = ?", (code,)
            ).fetchone()[0]
            click_rows = connection.execute(
                "SELECT clicked_at FROM clicks WHERE code = ? ORDER BY id DESC LIMIT ?",
                (code, recent_limit),
            ).fetchall()
            daily_rows = connection.execute(
                """
                SELECT substr(clicked_at, 1, 10) AS day, COUNT(*) AS click_count
                FROM clicks
                WHERE code = ?
                GROUP BY substr(clicked_at, 1, 10)
                ORDER BY day DESC
                LIMIT ?
                """,
                (code, recent_limit),
            ).fetchall()

        return LinkStatsRecord(
            link=self._link(link_row),
            click_count=count,
            recent_clicks=tuple(row[0] for row in click_rows),
            daily_clicks=tuple(
                DailyClickCount(day=row["day"], click_count=row["click_count"])
                for row in daily_rows
            ),
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.row_factory = sqlite3.Row
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _link(row: sqlite3.Row) -> LinkRecord:
        return LinkRecord(
            code=row["code"], destination=row["destination"], created_at=row["created_at"]
        )
