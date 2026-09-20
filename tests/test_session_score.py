"""Tests for POST /session/score (live pipeline contract)."""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.test_live_signals import cleanup_user

client = TestClient(app)


def test_session_score_returns_live_response() -> None:
    user_id = f"test-contract-{uuid.uuid4().hex[:12]}"
    payload = {
        "user_id": user_id,
        "ip_address": "192.168.1.10",
        "device_fingerprint": "example-device-hash",
    }
    try:
        response = client.post("/session/score", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body["risk_score"], int)
        assert isinstance(body["risk_tier"], str)
        assert body["risk_tier"] in {"low", "medium", "high"}
        signals = body["contributing_signals"]
        assert signals["geo_velocity_kmh"] == 0.0
        assert signals["geo_location_status"] == "no_history"  # fresh user
        assert signals["device_mismatch_score"] == 0.0
        assert signals["token_reuse_flag"] is False
        assert signals["login_burst_count"] == 1
        assert isinstance(body["session_id"], str) and body["session_id"]
    finally:
        cleanup_user(user_id)


def test_session_score_rejects_invalid_request() -> None:
    # Missing required fields -> validation error.
    response = client.post("/session/score", json={"user_id": "user123"})
    assert response.status_code == 422
    assert "detail" in response.json()
