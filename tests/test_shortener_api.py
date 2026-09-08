from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.codes import Sha256CodeGenerator
from app.main import create_app
from orchestrator.clock import FixedClock


class FirstCandidateCollision:
    def candidate(self, destination: str, collision_index: int) -> str:
        if collision_index == 0:
            return "sharedcode"
        return Sha256CodeGenerator().candidate(destination, collision_index)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "links.db", clock=FixedClock())) as test_client:
        yield test_client


def test_create_redirect_stats_end_to_end(client):
    created = client.post(
        "/links", json={"destination": "https://example.com/articles/one"}
    )
    assert created.status_code == 201
    link = created.json()
    assert created.headers["location"] == f"/{link['code']}"
    assert link["destination"] == "https://example.com/articles/one"

    redirected = client.get(f"/{link['code']}", follow_redirects=False)
    assert redirected.status_code == 307
    assert redirected.headers["location"] == link["destination"]

    stats = client.get(f"/links/{link['code']}/stats")
    assert stats.status_code == 200
    assert stats.json() == {
        "code": link["code"],
        "destination": link["destination"],
        "click_count": 1,
        "recent_clicks": [{"occurred_at": "2026-01-01T00:00:01+00:00"}],
        "daily_clicks": [{"day": "2026-01-01", "click_count": 1}],
    }


def test_create_is_idempotent_at_http_boundary(client):
    request = {"destination": "https://example.com/idempotent"}

    first = client.post("/links", json=request)
    second = client.post("/links", json=request)

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()


def test_api_handles_a_deterministic_short_code_collision(tmp_path):
    application = create_app(
        tmp_path / "links.db",
        clock=FixedClock(),
        code_generator=FirstCandidateCollision(),
    )
    with TestClient(application) as collision_client:
        first = collision_client.post(
            "/links", json={"destination": "https://example.com/first"}
        ).json()
        second = collision_client.post(
            "/links", json={"destination": "https://example.com/second"}
        ).json()

    assert first["code"] == "sharedcode"
    assert second["code"] != first["code"]


@pytest.mark.parametrize(
    "destination",
    [
        "",
        "not-a-url",
        "ftp://example.com/file",
        "javascript:alert(1)",
        "data:text/plain,hello",
        "https://user:secret@example.com/private",
    ],
)
def test_invalid_or_unsafe_destinations_are_rejected(client, destination):
    response = client.post("/links", json={"destination": destination})
    assert response.status_code == 422


def test_unknown_codes_are_predictably_not_found(client):
    redirect = client.get("/does-not-exist", follow_redirects=False)
    stats = client.get("/links/does-not-exist/stats")

    assert redirect.status_code == stats.status_code == 404
    assert redirect.json() == stats.json() == {"detail": "link not found"}


def test_health_readiness_and_openapi_contract(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}

    contract = client.get("/openapi.json")
    assert contract.status_code == 200
    assert {
        "/links",
        "/links/{code}/stats",
        "/health",
        "/ready",
        "/{code}",
    }.issubset(contract.json()["paths"])
