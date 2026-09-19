"""Tests for POST /session/score."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

VALID_PAYLOAD = {
    "user_id": "user123",
    "ip_address": "192.168.1.10",
    "device_fingerprint": "example-device-hash",
}


def test_session_score_returns_stub_response() -> None:
    response = client.post("/session/score", json=VALID_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert body == {"risk_score": 0, "risk_tier": "low"}
    assert isinstance(body["risk_score"], int)
    assert isinstance(body["risk_tier"], str)


def test_session_score_rejects_invalid_request() -> None:
    # Missing required fields -> validation error.
    response = client.post("/session/score", json={"user_id": "user123"})
    assert response.status_code == 422
    assert "detail" in response.json()
