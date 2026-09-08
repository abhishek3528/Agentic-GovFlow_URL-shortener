"""FastAPI entry point for the URL-shortener service."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.responses import RedirectResponse

from orchestrator.clock import Clock, SystemClock

from .codes import CodeGenerator, Sha256CodeGenerator
from .models import (
    ClickEventResponse,
    CreateLinkRequest,
    DailyClickCountResponse,
    HealthResponse,
    LinkResponse,
    LinkStatsResponse,
)
from .repository import CollisionLimitExceeded, LinkRecord, SqliteLinkRepository
from .service import ShortenerService


def create_app(
    database_path: str | Path | None = None,
    *,
    clock: Clock | None = None,
    code_generator: CodeGenerator | None = None,
) -> FastAPI:
    configured_path = database_path or os.environ.get(
        "URL_SHORTENER_DB", str(Path("data") / "links.db")
    )
    repository = SqliteLinkRepository(configured_path)
    service = ShortenerService(
        repository=repository,
        code_generator=code_generator or Sha256CodeGenerator(),
        clock=clock or SystemClock(),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        repository.initialize()
        yield

    application = FastAPI(
        title="Governed URL Shortener",
        version="1.0.0",
        description="A deterministic, SQLite-backed URL-shortener service.",
        lifespan=lifespan,
    )
    application.state.repository = repository
    application.state.shortener = service

    @application.post(
        "/links",
        response_model=LinkResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["links"],
    )
    def create_link(request: CreateLinkRequest, response: Response) -> LinkResponse:
        try:
            link = service.create(str(request.destination))
        except CollisionLimitExceeded as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="short-code capacity temporarily exhausted",
            ) from exc
        response.headers["Location"] = f"/{link.code}"
        return _link_response(link)

    @application.get(
        "/links/{code}/stats", response_model=LinkStatsResponse, tags=["analytics"]
    )
    def link_stats(code: str) -> LinkStatsResponse:
        stats = service.stats(code)
        if stats is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="link not found")
        return LinkStatsResponse(
            code=stats.link.code,
            destination=stats.link.destination,
            click_count=stats.click_count,
            recent_clicks=tuple(
                ClickEventResponse(occurred_at=timestamp) for timestamp in stats.recent_clicks
            ),
            daily_clicks=tuple(
                DailyClickCountResponse(day=item.day, click_count=item.click_count)
                for item in stats.daily_clicks
            ),
        )

    @application.get("/health", response_model=HealthResponse, tags=["operations"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.get(
        "/ready",
        response_model=HealthResponse,
        responses={503: {"description": "SQLite is unavailable"}},
        tags=["operations"],
    )
    def ready() -> HealthResponse:
        if not repository.ready():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="database unavailable",
            )
        return HealthResponse(status="ready")

    @application.get("/{code}", include_in_schema=True, tags=["links"])
    def redirect(code: str) -> RedirectResponse:
        resolved = service.resolve(code)
        if resolved is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="link not found")
        return RedirectResponse(
            url=resolved.destination, status_code=status.HTTP_307_TEMPORARY_REDIRECT
        )

    return application


def _link_response(link: LinkRecord) -> LinkResponse:
    return LinkResponse(
        code=link.code, destination=link.destination, created_at=link.created_at
    )


app = create_app()
