from fastapi.testclient import TestClient

from backend.api.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"  # still the M0 contract; M8 adds readiness and stage timings
    assert "ready" in body and "stages" in body
