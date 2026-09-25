from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_health_reports_engine_handshake(worker_script: Path) -> None:
    from animator.api.app import app

    with TestClient(app) as client:
        res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["engine"]["protocolVersion"].startswith("1.")
