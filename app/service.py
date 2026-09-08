"""Application behavior independent of HTTP transport."""

from __future__ import annotations

from dataclasses import dataclass

from .codes import CodeGenerator
from .clock import Clock
from .repository import LinkRecord, LinkStatsRecord, SqliteLinkRepository


@dataclass(frozen=True)
class ResolvedLink:
    destination: str


class ShortenerService:
    def __init__(
        self,
        repository: SqliteLinkRepository,
        code_generator: CodeGenerator,
        clock: Clock,
    ) -> None:
        self.repository = repository
        self.code_generator = code_generator
        self.clock = clock

    def create(self, destination: str) -> LinkRecord:
        return self.repository.create_or_get(
            destination=destination,
            created_at=self.clock.iso(),
            candidate_for=lambda collision_index: self.code_generator.candidate(
                destination, collision_index
            ),
        )

    def resolve(self, code: str) -> ResolvedLink | None:
        link = self.repository.get(code)
        if link is None:
            return None
        if not self.repository.record_click(code, self.clock.iso()):
            return None
        return ResolvedLink(destination=link.destination)

    def stats(self, code: str) -> LinkStatsRecord | None:
        return self.repository.stats(code)
