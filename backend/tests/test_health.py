"""Smoke test for the /health liveness probe."""
from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def test_health_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
