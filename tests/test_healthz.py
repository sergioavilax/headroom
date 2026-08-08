"""The one route Phase 0 ships."""

from fastapi.testclient import TestClient

from headroom.api.main import app


def test_healthz_returns_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
