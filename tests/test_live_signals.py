"""Live risk-signal pipeline tests.

Runs inside the Docker Compose stack against the real Postgres and Redis
services; GeoLite2 lookups are monkeypatched (or intentionally pointed at a
missing file). All database/Redis state created here is cleaned up per test.
"""

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models import RiskEvent, Session, User
from app.services import geo, risk_pipeline
from app.services.baseline_scorer import score_session as frozen_score_session

client = TestClient(app)

_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)

# Fake GeoLite2 records for public IPs: 8.8.8.8 and 9.9.9.9 share a location,
# 1.1.1.1 sits far away. IPs absent from the map -> "not found in database".
_FAKE_COORDS: dict[str, tuple[float, float]] = {
    "8.8.8.8": (37.7510, -97.8220),
    "9.9.9.9": (37.7510, -97.8220),
    "1.1.1.1": (51.5085, -0.1257),
}


def _fake_geolocate(ip: str) -> tuple[float, float] | None:
    return _FAKE_COORDS.get(ip)


def cleanup_user(user_id: str) -> None:
    """Remove all Postgres rows and Redis keys created for a test user."""
    session_ids: list[str] = []
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(User.user_id == user_id)
        ).scalar_one_or_none()
        if user is not None:
            rows = (
                db.execute(select(Session).where(Session.user_id == user.id))
                .scalars()
                .all()
            )
            session_ids = [row.session_id for row in rows]
            if rows:
                row_ids = [row.id for row in rows]
                db.execute(delete(RiskEvent).where(RiskEvent.session_id.in_(row_ids)))
                db.execute(delete(Session).where(Session.id.in_(row_ids)))
            db.execute(delete(User).where(User.id == user.id))
            db.commit()
    _redis.delete(f"ate:login_burst:{user_id}")
    keys: list[str] = []
    for session_id in session_ids:
        keys.extend(
            [
                f"session:{session_id}:refresh_token_hash",
                f"session:{session_id}:context",
            ]
        )
    if keys:
        _redis.delete(*keys)


@pytest.fixture
def user_id() -> str:
    value = f"test-live-{uuid.uuid4().hex[:12]}"
    yield value
    cleanup_user(value)


@pytest.fixture
def fake_geo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geo, "geolocate", _fake_geolocate)


def _payload(
    user_id: str,
    *,
    ip_address: str = "192.168.1.10",
    device_fingerprint: str = "device-A",
    refresh_token: str | None = None,
) -> dict[str, str]:
    payload = {
        "user_id": user_id,
        "ip_address": ip_address,
        "device_fingerprint": device_fingerprint,
    }
    if refresh_token is not None:
        payload["refresh_token"] = refresh_token
    return payload


def _seed_previous_session(
    user_id: str,
    device_fingerprint: str,
    ip_address: str,
    last_seen_at: datetime,
) -> None:
    """Insert the user (if absent) plus one previous session row directly."""
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(User.user_id == user_id)
        ).scalar_one_or_none()
        if user is None:
            user = User(user_id=user_id)
            db.add(user)
            db.flush()
        db.add(
            Session(
                session_id=str(uuid.uuid4()),
                user_id=user.id,
                device_fingerprint=device_fingerprint,
                ip_address=ip_address,
                created_at=last_seen_at,
                last_seen_at=last_seen_at,
            )
        )
        db.commit()


def _session_count(user_id: str) -> int:
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(User.user_id == user_id)
        ).scalar_one_or_none()
        if user is None:
            return 0
        return len(
            db.execute(select(Session).where(Session.user_id == user.id))
            .scalars()
            .all()
        )


def _assert_score_consistent(body: dict) -> None:
    """The response score/tier must equal the frozen scorer applied to the
    response's own reported signals."""
    signals = body["contributing_signals"]
    raw, tier, _ = frozen_score_session(
        signals["geo_velocity_kmh"],
        signals["device_mismatch_score"],
        signals["token_reuse_flag"],
        signals["login_burst_count"],
    )
    assert body["risk_tier"] == tier
    assert body["risk_score"] == int(round(raw))


def test_first_ever_session(user_id: str) -> None:
    response = client.post("/session/score", json=_payload(user_id))
    assert response.status_code == 200
    body = response.json()
    assert body["contributing_signals"] == {
        "geo_velocity_kmh": 0.0,
        "geo_location_status": "no_history",
        "device_mismatch_score": 0.0,
        "token_reuse_flag": False,
        "login_burst_count": 1,
    }
    assert body["risk_tier"] == "low"
    assert isinstance(body["session_id"], str) and body["session_id"]
    _assert_score_consistent(body)


def test_same_device_no_mismatch(user_id: str, fake_geo: None) -> None:
    _seed_previous_session(
        user_id, "device-A", "8.8.8.8", datetime.now(timezone.utc) - timedelta(hours=1)
    )
    response = client.post(
        "/session/score",
        json=_payload(user_id, ip_address="1.1.1.1", device_fingerprint="device-A"),
    )
    assert response.status_code == 200
    signals = response.json()["contributing_signals"]
    assert signals["device_mismatch_score"] == 0.0
    assert signals["geo_location_status"] == "ok"


def test_changed_device_flagged(user_id: str, fake_geo: None) -> None:
    _seed_previous_session(
        user_id, "device-A", "8.8.8.8", datetime.now(timezone.utc) - timedelta(hours=1)
    )
    response = client.post(
        "/session/score",
        json=_payload(user_id, ip_address="1.1.1.1", device_fingerprint="device-B"),
    )
    assert response.status_code == 200
    signals = response.json()["contributing_signals"]
    assert signals["device_mismatch_score"] == 1.0
    assert signals["geo_location_status"] == "ok"
    _assert_score_consistent(response.json())


def test_private_ip_fallback(user_id: str) -> None:
    _seed_previous_session(
        user_id,
        "device-A",
        "192.168.1.5",
        datetime.now(timezone.utc) - timedelta(hours=1),
    )
    response = client.post(
        "/session/score", json=_payload(user_id, ip_address="192.168.1.10")
    )
    assert response.status_code == 200
    signals = response.json()["contributing_signals"]
    assert signals["geo_velocity_kmh"] == 0.0
    assert signals["geo_location_status"] == "private_ip"


def test_invalid_ip_status(user_id: str) -> None:
    _seed_previous_session(
        user_id,
        "device-A",
        "not-an-ip",
        datetime.now(timezone.utc) - timedelta(hours=1),
    )
    response = client.post(
        "/session/score", json=_payload(user_id, ip_address="192.168.1.10")
    )
    assert response.status_code == 200
    signals = response.json()["contributing_signals"]
    assert signals["geo_velocity_kmh"] == 0.0
    assert signals["geo_location_status"] == "invalid_ip"


def test_geolocation_not_found(user_id: str, fake_geo: None) -> None:
    # 93.184.216.34 is absent from the fake database -> not found.
    _seed_previous_session(
        user_id,
        "device-A",
        "93.184.216.34",
        datetime.now(timezone.utc) - timedelta(hours=1),
    )
    response = client.post(
        "/session/score", json=_payload(user_id, ip_address="8.8.8.8")
    )
    assert response.status_code == 200
    signals = response.json()["contributing_signals"]
    assert signals["geo_velocity_kmh"] == 0.0
    assert signals["geo_location_status"] == "geolocation_not_found"


def test_missing_geolite2_database_returns_503(
    user_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "geoip_db_path", "data/geoip/does-not-exist.mmdb")
    _seed_previous_session(
        user_id, "device-A", "8.8.8.8", datetime.now(timezone.utc) - timedelta(hours=1)
    )
    response = client.post(
        "/session/score", json=_payload(user_id, ip_address="1.1.1.1")
    )
    assert response.status_code == 503
    # Fixed generic body: no server paths, SQL, or database internals leak.
    assert response.json() == {
        "detail": "Scoring service temporarily unavailable. Please try again later."
    }
    assert _session_count(user_id) == 1  # only the seeded previous session


def test_same_location_zero_velocity(user_id: str, fake_geo: None) -> None:
    _seed_previous_session(
        user_id, "device-A", "8.8.8.8", datetime.now(timezone.utc) - timedelta(hours=1)
    )
    response = client.post(
        "/session/score", json=_payload(user_id, ip_address="9.9.9.9")
    )
    assert response.status_code == 200
    signals = response.json()["contributing_signals"]
    assert signals["geo_location_status"] == "ok"
    assert signals["geo_velocity_kmh"] == 0.0


def test_elapsed_time_non_positive(user_id: str) -> None:
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    _seed_previous_session(user_id, "device-A", "8.8.8.8", future)
    response = client.post(
        "/session/score", json=_payload(user_id, ip_address="1.1.1.1")
    )
    assert response.status_code == 200
    signals = response.json()["contributing_signals"]
    assert signals["geo_velocity_kmh"] == 0.0
    assert signals["geo_location_status"] == "invalid_elapsed_time"


def test_stale_previous_session(user_id: str, fake_geo: None) -> None:
    _seed_previous_session(
        user_id, "device-A", "8.8.8.8", datetime.now(timezone.utc) - timedelta(days=14)
    )
    response = client.post(
        "/session/score", json=_payload(user_id, ip_address="1.1.1.1")
    )
    assert response.status_code == 200
    signals = response.json()["contributing_signals"]
    assert signals["geo_location_status"] == "ok"
    # ~7000 km over 14 days -> a small positive velocity, no special-casing.
    assert 0.0 < signals["geo_velocity_kmh"] < 50.0


def test_rolling_burst_window(user_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(risk_pipeline, "LOGIN_BURST_WINDOW_SECONDS", 2)
    counts = []
    for _ in range(3):
        response = client.post("/session/score", json=_payload(user_id))
        counts.append(response.json()["contributing_signals"]["login_burst_count"])
    assert counts == [1, 2, 3]
    time.sleep(2.2)
    response = client.post("/session/score", json=_payload(user_id))
    assert response.json()["contributing_signals"]["login_burst_count"] == 1


def test_token_reuse_detection(user_id: str) -> None:
    first = client.post("/session/score", json=_payload(user_id, refresh_token="tok-abc"))
    assert first.json()["contributing_signals"]["token_reuse_flag"] is False

    same_token = client.post(
        "/session/score", json=_payload(user_id, refresh_token="tok-abc")
    )
    assert same_token.json()["contributing_signals"]["token_reuse_flag"] is True

    new_token = client.post(
        "/session/score", json=_payload(user_id, refresh_token="tok-xyz")
    )
    assert new_token.json()["contributing_signals"]["token_reuse_flag"] is False

    reused_new = client.post(
        "/session/score", json=_payload(user_id, refresh_token="tok-xyz")
    )
    assert reused_new.json()["contributing_signals"]["token_reuse_flag"] is True


def test_postgres_persistence_failure_returns_503(
    user_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Postgres failure during persistence -> 503; no Redis post-writes, no rows."""

    class _FailOnSecondSessionLocal:
        def __init__(self, factory):
            self._factory = factory
            self._calls = 0

        def __call__(self):
            self._calls += 1
            if self._calls >= 2:
                raise OperationalError("simulated failure", None, None)
            return self._factory()

    redis_updates: list[tuple] = []
    monkeypatch.setattr(
        risk_pipeline,
        "SessionLocal",
        _FailOnSecondSessionLocal(risk_pipeline.SessionLocal),
    )
    monkeypatch.setattr(
        risk_pipeline,
        "_update_redis_after_commit",
        lambda *args, **kwargs: redis_updates.append(args),
    )

    response = client.post("/session/score", json=_payload(user_id))
    assert response.status_code == 503
    # Fixed generic body: the driver/exception details stay server-side only.
    assert response.json() == {
        "detail": "Scoring service temporarily unavailable. Please try again later."
    }
    assert redis_updates == []  # Redis post-writes never attempted
    assert _session_count(user_id) == 0  # nothing partially persisted


def test_post_commit_redis_failure_is_fail_soft(
    user_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis failing AFTER the Postgres commit -> logged, request still succeeds."""

    def _boom(*args, **kwargs):
        raise redis.RedisError("simulated redis outage")

    context_updates: list[tuple] = []
    monkeypatch.setattr(risk_pipeline, "store_refresh_token_hash", _boom)
    monkeypatch.setattr(
        risk_pipeline,
        "update_session_context",
        lambda *args, **kwargs: context_updates.append(args),
    )

    response = client.post(
        "/session/score", json=_payload(user_id, refresh_token="tok-failsoft")
    )
    assert response.status_code == 200
    assert response.json()["risk_tier"] in {"low", "medium", "high"}
    assert context_updates != []  # one failed write does not skip the other
    assert _session_count(user_id) == 1  # session durably persisted
