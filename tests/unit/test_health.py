from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from enterprise_context.api import app

client = TestClient(app)


def test_liveness_does_not_depend_on_database() -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"
    assert "x-request-id" in response.headers


def test_request_id_is_preserved() -> None:
    response = client.get("/health/live", headers={"x-request-id": "test-request-123"})

    assert response.headers["x-request-id"] == "test-request-123"


def test_metrics_endpoint_is_exposed() -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "http_requests_total" in response.text


def test_readiness_returns_unavailable_when_database_is_down(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("enterprise_context.api.check_database", lambda: False)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "database_unavailable"


def test_readiness_returns_ready_when_database_is_available(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("enterprise_context.api.check_database", lambda: True)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
