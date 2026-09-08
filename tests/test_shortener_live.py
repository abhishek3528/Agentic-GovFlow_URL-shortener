from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


def test_service_starts_and_serves_live_smoke_path(tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    environment = os.environ.copy()
    environment["URL_SHORTENER_DB"] = str(tmp_path / "live.db")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=repository_root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    base_url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=base_url, follow_redirects=False, timeout=1) as client:
            deadline = time.monotonic() + 10
            while True:
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if process.poll() is not None:
                    output = process.stdout.read() if process.stdout else ""
                    raise AssertionError(f"live server exited early:\n{output}")
                if time.monotonic() >= deadline:
                    raise AssertionError("live server did not become healthy within 10 seconds")
                time.sleep(0.05)

            created = client.post(
                "/links", json={"destination": "https://example.com/live-smoke"}
            )
            assert created.status_code == 201
            code = created.json()["code"]
            assert client.get(f"/{code}").status_code == 307
            assert client.get(f"/links/{code}/stats").json()["click_count"] == 1
            assert client.get("/ready").json() == {"status": "ready"}
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
