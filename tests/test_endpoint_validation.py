"""Extended endpoint tests: boundary, combination, malformed-input, concurrency.

Companion module to tests/test_live_signals.py (which covers per-signal
behavior). This module adds:

- boundary tests engineered to hit *exact* unrounded tier boundaries
  (40.0 -> low, 40.01 -> medium, 70.0 -> medium, 70.01 -> high) through the
  live pipeline, with the frozen scorer as the oracle;
- signal-combination tests asserting the full chain: engineered signals ->
  frozen formula -> expected score/tier -> API response -> persisted
  risk_events row (including the unrounded score);
- malformed/invalid-input tests (422 validation, malformed IPs, garbage
  tokens); empty strings and oversized values are rejected by the explicit
  min_length/max_length constraints on the request schema;
- concurrency tests firing real HTTP requests at the running in-container
  uvicorn (Dockerfile: port 8000; the compose 8008:8000 mapping is host-side)
  via ThreadPoolExecutor + a Barrier, asserting API-vs-database consistency;
  the fresh-user variant is the regression lock for the fixed find-or-create
  race (all 200 responses, single user row);
- a test forcing a real persistence IntegrityError and asserting that the
  503 body exposes no SQL/table/constraint details (full error logged
  server-side only);
- a test documenting the rounded-display-score vs unrounded-tier mismatch.

Runs inside the Docker Compose stack against the real Postgres and Redis
(established runner: ``docker compose exec -T api python -m pytest``). Every
test creates unique user ids and cleans up all Postgres rows and Redis keys
via fixtures / try-finally, including when assertions fail.
"""

import hashlib
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models import RiskEvent, Session, User
from app.services import geo
from app.services.baseline_scorer import (
    classify_tier,
    score_session as frozen_score_session,
)
from app.services.session_store import store_refresh_token_hash

client = TestClient(app)

_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)

# uvicorn listens on port 8000 *inside* the api container (Dockerfile CMD);
# the compose port mapping 8008:8000 is host-side only.
API_BASE_URL = "http://localhost:8000"
CONCURRENT_REQUESTS = 10

DOMAIN_DEVICE = "device-A"


# ---------------------------------------------------------------------------
# State helpers (same pattern as tests/test_live_signals.py)
# ---------------------------------------------------------------------------


def cleanup_user(user_id: str) -> None:
    """Remove every Postgres row and Redis key a test created for this user."""
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
    value = f"test-endpoint-{uuid.uuid4().hex[:12]}"
    yield value
    cleanup_user(value)  # teardown runs even when assertions fail


def _payload(
    user_id: str,
    *,
    ip_address: str = "192.168.1.10",
    device_fingerprint: str = DOMAIN_DEVICE,
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
    session_id: str | None = None,
) -> str:
    """Insert the user (if absent) plus one previous session; return its id."""
    session_id = session_id or str(uuid.uuid4())
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
                session_id=session_id,
                user_id=user.id,
                device_fingerprint=device_fingerprint,
                ip_address=ip_address,
                created_at=last_seen_at,
                last_seen_at=last_seen_at,
            )
        )
        db.commit()
    return session_id


def _seed_login_attempts(user_id: str, count: int) -> None:
    """Add `count` burst members scored 'now' (inside the rolling 60s window)."""
    key = f"ate:login_burst:{user_id}"
    now_ts = time.time()
    _redis.zadd(key, {f"seed-{uuid.uuid4().hex}": now_ts for _ in range(count)})


def _store_token_for_session(session_id: str, raw_token: str) -> None:
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    store_refresh_token_hash(session_id, token_hash)


def _user_row_count(user_id: str) -> int:
    with SessionLocal() as db:
        rows = (
            db.execute(select(User).where(User.user_id == user_id)).scalars().all()
        )
        return len(rows)


def _user_count_total() -> int:
    with SessionLocal() as db:
        return db.execute(select(func.count()).select_from(User)).scalar_one()


def _risk_event_count_total() -> int:
    with SessionLocal() as db:
        return db.execute(select(func.count()).select_from(RiskEvent)).scalar_one()


def _risk_event_count_for_user(user_id: str) -> int:
    with SessionLocal() as db:
        return db.execute(
            select(func.count())
            .select_from(RiskEvent)
            .join(Session, RiskEvent.session_id == Session.id)
            .join(User, Session.user_id == User.id)
            .where(User.user_id == user_id)
        ).scalar_one()


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


# ---------------------------------------------------------------------------
# Consistency helpers (API response <-> frozen scorer <-> persisted row)
# ---------------------------------------------------------------------------


def _assert_frozen_consistency(body: dict) -> None:
    """The response's own signals must reproduce its score/tier via the frozen scorer."""
    signals = body["contributing_signals"]
    raw, tier, _ = frozen_score_session(
        signals["geo_velocity_kmh"],
        signals["device_mismatch_score"],
        signals["token_reuse_flag"],
        signals["login_burst_count"],
    )
    assert body["risk_tier"] == tier
    assert body["risk_score"] == int(round(raw))


def _assert_response_matches_persisted(body: dict) -> None:
    """Close the loop: API response == persisted risk_events row."""
    with SessionLocal() as db:
        session_row = db.execute(
            select(Session).where(Session.session_id == body["session_id"])
        ).scalar_one()
        event = db.execute(
            select(RiskEvent).where(RiskEvent.session_id == session_row.id)
        ).scalar_one()
        persisted = event.contributing_signals
    assert event.risk_score == body["risk_score"]
    assert event.risk_tier == body["risk_tier"]
    signals = body["contributing_signals"]
    assert set(persisted) == set(signals) | {"risk_score_unrounded"}
    for key, value in signals.items():
        if isinstance(value, float):
            assert persisted[key] == pytest.approx(value, rel=1e-9, abs=1e-9)
        else:
            assert persisted[key] == value
    raw, tier, _ = frozen_score_session(
        signals["geo_velocity_kmh"],
        signals["device_mismatch_score"],
        signals["token_reuse_flag"],
        signals["login_burst_count"],
    )
    assert persisted["risk_score_unrounded"] == pytest.approx(raw, rel=1e-9, abs=1e-9)
    assert int(round(persisted["risk_score_unrounded"])) == body["risk_score"]
    assert body["risk_tier"] == tier
    _assert_frozen_consistency(body)


# ---------------------------------------------------------------------------
# Boundary tests: engineered exact unrounded scores at the tier thresholds
#
# Effective frozen formula (inspected):
#   raw = 30*min(v/1000, 1) + 30*dm + 25*tok + 15*min(burst/30, 1)
# Tier classification uses `<=` on the *unrounded* raw score.
# Arrangement below yields token_reuse=True (25) and login_burst_count=30
# (15 => exactly 15.0), so:
#   v=0.0, dm=0.0 -> 40.0 exactly      v=1/3, dm=0.0 -> 40.010000000000005
#   v=0.0, dm=1.0 -> 70.0 exactly      v=1/3, dm=1.0 -> 70.01
# ---------------------------------------------------------------------------


def _install_fake_geo(monkeypatch: pytest.MonkeyPatch, velocity_kmh: float) -> None:
    """Control the live geo-velocity computation (documented test mechanism)."""

    def _fake_compute_geo_velocity(
        previous_ip: str | None,
        previous_last_seen_at: datetime | None,
        current_ip: str,
        current_time: datetime,
    ) -> tuple[float, str, tuple[float, float] | None]:
        return velocity_kmh, "ok", (37.7510, -97.8220)

    monkeypatch.setattr(geo, "compute_geo_velocity", _fake_compute_geo_velocity)


def _arrange_boundary_user(
    user_id: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    previous_device: str,
    velocity_kmh: float,
) -> None:
    """Previous session + stored token + 29 seeded attempts + controlled geo.

    A subsequent live request then sees token_reuse=True, login_burst_count=30
    (29 seeded + its own attempt), and the controlled geo velocity;
    device_mismatch is 0.0 when `previous_device` equals the request's
    fingerprint ("device-A"), else 1.0.
    """
    previous_session_id = _seed_previous_session(
        user_id,
        previous_device,
        "192.168.1.5",
        datetime.now(timezone.utc) - timedelta(hours=1),
    )
    _store_token_for_session(previous_session_id, "tok-boundary")
    _seed_login_attempts(user_id, 29)
    _install_fake_geo(monkeypatch, velocity_kmh)


def test_boundary_exact_40_0_is_low(user_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unrounded score exactly 40.0 -> "low" (the <= boundary holds)."""
    _arrange_boundary_user(
        user_id, monkeypatch, previous_device=DOMAIN_DEVICE, velocity_kmh=0.0
    )
    expected_raw, expected_tier, _ = frozen_score_session(0.0, 0.0, True, 30)
    assert expected_raw == 40.0  # the construction hits the boundary exactly
    assert expected_tier == "low"

    response = client.post(
        "/session/score", json=_payload(user_id, refresh_token="tok-boundary")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["contributing_signals"] == {
        "geo_velocity_kmh": 0.0,
        "geo_location_status": "ok",
        "device_mismatch_score": 0.0,
        "token_reuse_flag": True,
        "login_burst_count": 30,
    }
    assert body["risk_score"] == 40
    assert body["risk_tier"] == "low"
    _assert_response_matches_persisted(body)


def test_boundary_40_01_is_medium(user_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unrounded score just above 40 (40.01) -> "medium"."""
    _arrange_boundary_user(
        user_id, monkeypatch, previous_device=DOMAIN_DEVICE, velocity_kmh=1 / 3
    )
    expected_raw, expected_tier, _ = frozen_score_session(1 / 3, 0.0, True, 30)
    assert expected_raw > 40.0
    assert expected_raw == pytest.approx(40.01, rel=1e-9)
    assert expected_tier == "medium"

    response = client.post(
        "/session/score", json=_payload(user_id, refresh_token="tok-boundary")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["contributing_signals"] == {
        "geo_velocity_kmh": pytest.approx(1 / 3),
        "geo_location_status": "ok",
        "device_mismatch_score": 0.0,
        "token_reuse_flag": True,
        "login_burst_count": 30,
    }
    assert body["risk_score"] == 40  # rounds to 40 while the tier is medium
    assert body["risk_tier"] == "medium"
    _assert_response_matches_persisted(body)


def test_boundary_exact_70_0_is_medium(user_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unrounded score exactly 70.0 -> "medium" (the <= boundary holds)."""
    _arrange_boundary_user(
        user_id, monkeypatch, previous_device="device-OTHER", velocity_kmh=0.0
    )
    expected_raw, expected_tier, _ = frozen_score_session(0.0, 1.0, True, 30)
    assert expected_raw == 70.0  # the construction hits the boundary exactly
    assert expected_tier == "medium"

    response = client.post(
        "/session/score", json=_payload(user_id, refresh_token="tok-boundary")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["contributing_signals"] == {
        "geo_velocity_kmh": 0.0,
        "geo_location_status": "ok",
        "device_mismatch_score": 1.0,
        "token_reuse_flag": True,
        "login_burst_count": 30,
    }
    assert body["risk_score"] == 70
    assert body["risk_tier"] == "medium"
    _assert_response_matches_persisted(body)


def test_boundary_70_01_is_high(user_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unrounded score just above 70 (70.01) -> "high"."""
    _arrange_boundary_user(
        user_id, monkeypatch, previous_device="device-OTHER", velocity_kmh=1 / 3
    )
    expected_raw, expected_tier, _ = frozen_score_session(1 / 3, 1.0, True, 30)
    assert expected_raw > 70.0
    assert expected_raw == pytest.approx(70.01, rel=1e-9)
    assert expected_tier == "high"

    response = client.post(
        "/session/score", json=_payload(user_id, refresh_token="tok-boundary")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["contributing_signals"] == {
        "geo_velocity_kmh": pytest.approx(1 / 3),
        "geo_location_status": "ok",
        "device_mismatch_score": 1.0,
        "token_reuse_flag": True,
        "login_burst_count": 30,
    }
    assert body["risk_score"] == 70  # rounds to 70 while the tier is high
    assert body["risk_tier"] == "high"
    _assert_response_matches_persisted(body)


# ---------------------------------------------------------------------------
# Signal-combination tests (full chain, geo kept clean via the private IP)
# ---------------------------------------------------------------------------


def _arrange_combo(
    user_id: str,
    *,
    previous_device: str,
    with_token: bool,
    extra_attempts: int,
) -> None:
    """Seed the previous session (device/token state) and burst attempts."""
    previous_session_id = _seed_previous_session(
        user_id,
        previous_device,
        "192.168.1.5",
        datetime.now(timezone.utc) - timedelta(hours=1),
    )
    if with_token:
        _store_token_for_session(previous_session_id, "tok-combo")
    if extra_attempts:
        _seed_login_attempts(user_id, extra_attempts)


def _post_combo(user_id: str, with_token: bool):
    refresh_token = "tok-combo" if with_token else None
    return client.post(
        "/session/score", json=_payload(user_id, refresh_token=refresh_token)
    )


def _assert_combo(
    response,
    *,
    device_mismatch: float,
    token_reuse: bool,
    burst_count: int,
) -> None:
    """Assert the full chain for one combination (nominal values in docstrings)."""
    assert response.status_code == 200
    body = response.json()
    engineered = {
        "geo_velocity_kmh": 0.0,
        "geo_location_status": "private_ip",
        "device_mismatch_score": device_mismatch,
        "token_reuse_flag": token_reuse,
        "login_burst_count": burst_count,
    }
    assert body["contributing_signals"] == engineered
    expected_raw, expected_tier, _ = frozen_score_session(
        0.0, device_mismatch, token_reuse, burst_count
    )
    assert body["risk_score"] == int(round(expected_raw))
    assert body["risk_tier"] == expected_tier
    _assert_response_matches_persisted(body)


def test_combo_device_change_only(user_id: str) -> None:
    """Device change only: dm 1.0, no token, burst 1 (nominal ~30.5, low)."""
    _arrange_combo(
        user_id, previous_device="device-B", with_token=False, extra_attempts=0
    )
    response = _post_combo(user_id, with_token=False)
    _assert_combo(response, device_mismatch=1.0, token_reuse=False, burst_count=1)


def test_combo_token_reuse_only(user_id: str) -> None:
    """Token reuse only: dm 0.0, token reuse, burst 1 (nominal ~25.5, low)."""
    _arrange_combo(
        user_id, previous_device=DOMAIN_DEVICE, with_token=True, extra_attempts=0
    )
    response = _post_combo(user_id, with_token=True)
    _assert_combo(response, device_mismatch=0.0, token_reuse=True, burst_count=1)


def test_combo_burst_elevation_only(user_id: str) -> None:
    """Burst only: dm 0.0, no token, 14 seeded + own = 15 (nominal ~7.5, low)."""
    _arrange_combo(
        user_id, previous_device=DOMAIN_DEVICE, with_token=False, extra_attempts=14
    )
    response = _post_combo(user_id, with_token=False)
    _assert_combo(response, device_mismatch=0.0, token_reuse=False, burst_count=15)


def test_combo_device_change_plus_token_reuse(user_id: str) -> None:
    """Device + token: dm 1.0, reuse, burst 1 (nominal ~55.5, medium)."""
    _arrange_combo(
        user_id, previous_device="device-B", with_token=True, extra_attempts=0
    )
    response = _post_combo(user_id, with_token=True)
    _assert_combo(response, device_mismatch=1.0, token_reuse=True, burst_count=1)


def test_combo_device_change_plus_burst(user_id: str) -> None:
    """Device + burst: dm 1.0, no token, burst 15 (nominal ~37.5, low)."""
    _arrange_combo(
        user_id, previous_device="device-B", with_token=False, extra_attempts=14
    )
    response = _post_combo(user_id, with_token=False)
    _assert_combo(response, device_mismatch=1.0, token_reuse=False, burst_count=15)


def test_combo_token_reuse_plus_burst(user_id: str) -> None:
    """Token + burst: dm 0.0, reuse, burst 15 (nominal ~32.5, low)."""
    _arrange_combo(
        user_id, previous_device=DOMAIN_DEVICE, with_token=True, extra_attempts=14
    )
    response = _post_combo(user_id, with_token=True)
    _assert_combo(response, device_mismatch=0.0, token_reuse=True, burst_count=15)


def test_combo_all_three_signals_elevated(user_id: str) -> None:
    """All three: dm 1.0, reuse, burst 15 (nominal ~62.5, medium)."""
    _arrange_combo(
        user_id, previous_device="device-B", with_token=True, extra_attempts=14
    )
    response = _post_combo(user_id, with_token=True)
    _assert_combo(response, device_mismatch=1.0, token_reuse=True, burst_count=15)


# ---------------------------------------------------------------------------
# Rounded display score vs unrounded tier classification
# ---------------------------------------------------------------------------


def test_rounded_score_vs_unrounded_tier_mismatch(
    user_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Documents that the rounded display score can look like a *lower* tier:

    - raw 40.010000000000005 displays as 40 (classifying the rounded value
      would say "low") but is classified "medium" on the unrounded score;
    - raw 70.01 displays as 70 (classifying the rounded value would say
      "medium") but is classified "high".

    Tier classification never uses the rounded score (frozen `classify_tier`
    is applied to the unrounded raw score).
    """
    # Case A: raw ~40.01 -> medium, display 40.
    _arrange_boundary_user(
        user_id, monkeypatch, previous_device=DOMAIN_DEVICE, velocity_kmh=1 / 3
    )
    raw_a, tier_a, _ = frozen_score_session(1 / 3, 0.0, True, 30)
    assert tier_a == "medium"
    assert classify_tier(float(int(round(raw_a)))) == "low"  # rounding-first view
    response_a = client.post(
        "/session/score", json=_payload(user_id, refresh_token="tok-boundary")
    )
    assert response_a.status_code == 200
    body_a = response_a.json()
    assert body_a["risk_score"] == 40
    assert body_a["risk_tier"] == "medium" == tier_a
    assert body_a["contributing_signals"]["device_mismatch_score"] == 0.0
    assert body_a["contributing_signals"]["token_reuse_flag"] is True
    assert body_a["contributing_signals"]["login_burst_count"] == 30
    _assert_response_matches_persisted(body_a)

    # Case B: raw 70.01 -> high, display 70. The previous session is now
    # case A's session (same device), so switching the request's device
    # fingerprint yields device_mismatch 1.0; the request reuses the token
    # stored for session A.
    _redis.delete(f"ate:login_burst:{user_id}")
    _seed_login_attempts(user_id, 29)
    raw_b, tier_b, _ = frozen_score_session(1 / 3, 1.0, True, 30)
    assert tier_b == "high"
    assert classify_tier(float(int(round(raw_b)))) == "medium"  # rounding-first view
    response_b = client.post(
        "/session/score",
        json=_payload(user_id, device_fingerprint="device-B", refresh_token="tok-boundary"),
    )
    assert response_b.status_code == 200
    body_b = response_b.json()
    assert body_b["risk_score"] == 70
    assert body_b["risk_tier"] == "high" == tier_b
    assert body_b["contributing_signals"]["device_mismatch_score"] == 1.0
    assert body_b["contributing_signals"]["token_reuse_flag"] is True
    assert body_b["contributing_signals"]["login_burst_count"] == 30
    _assert_response_matches_persisted(body_b)


# ---------------------------------------------------------------------------
# Malformed / invalid input
# ---------------------------------------------------------------------------


def test_validation_missing_user_id_returns_422_and_no_writes() -> None:
    events_before = _risk_event_count_total()
    users_before = _user_count_total()
    response = client.post(
        "/session/score",
        json={"ip_address": "192.168.1.10", "device_fingerprint": DOMAIN_DEVICE},
    )
    assert response.status_code == 422
    assert _risk_event_count_total() == events_before
    assert _user_count_total() == users_before


def test_validation_missing_ip_address_returns_422_and_no_writes() -> None:
    events_before = _risk_event_count_total()
    users_before = _user_count_total()
    response = client.post(
        "/session/score",
        json={"user_id": "no-ip-user", "device_fingerprint": DOMAIN_DEVICE},
    )
    assert response.status_code == 422
    assert _risk_event_count_total() == events_before
    assert _user_count_total() == users_before
    assert _user_row_count("no-ip-user") == 0


def test_validation_missing_device_fingerprint_returns_422_and_no_writes() -> None:
    events_before = _risk_event_count_total()
    users_before = _user_count_total()
    response = client.post(
        "/session/score",
        json={"user_id": "no-device-user", "ip_address": "192.168.1.10"},
    )
    assert response.status_code == 422
    assert _risk_event_count_total() == events_before
    assert _user_count_total() == users_before
    assert _user_row_count("no-device-user") == 0


def test_validation_wrong_types_return_422_and_no_writes(user_id: str) -> None:
    events_before = _risk_event_count_total()
    users_before = _user_count_total()
    int_user = client.post(
        "/session/score",
        json={
            "user_id": 123,
            "ip_address": "192.168.1.10",
            "device_fingerprint": DOMAIN_DEVICE,
        },
    )
    assert int_user.status_code == 422  # Pydantic does not coerce int -> str
    null_device = client.post(
        "/session/score",
        json={
            "user_id": user_id,
            "ip_address": "192.168.1.10",
            "device_fingerprint": None,
        },
    )
    assert null_device.status_code == 422
    assert _risk_event_count_total() == events_before
    assert _user_count_total() == users_before
    assert _user_row_count(user_id) == 0


def test_empty_string_fields_return_422() -> None:
    """Empty strings violate the schema's explicit min_length=1 constraints
    on all three required fields -> 422 and nothing persisted."""
    cleanup_user("")  # remove leftovers from any earlier failed run
    try:
        events_before = _risk_event_count_total()
        users_before = _user_count_total()
        response = client.post(
            "/session/score",
            json={"user_id": "", "ip_address": "", "device_fingerprint": ""},
        )
        assert response.status_code == 422
        assert _risk_event_count_total() == events_before
        assert _user_count_total() == users_before
    finally:
        cleanup_user("")


def test_oversized_user_id_returns_422() -> None:
    """A 5000-char user_id exceeds the schema's max_length=255 -> 422 and
    nothing persisted (DB columns stay unbounded; enforcement is schema-level
    only)."""
    long_user_id = "u" * 5000
    cleanup_user(long_user_id)
    try:
        events_before = _risk_event_count_total()
        users_before = _user_count_total()
        response = client.post(
            "/session/score",
            json={
                "user_id": long_user_id,
                "ip_address": "192.168.1.10",
                "device_fingerprint": DOMAIN_DEVICE,
            },
        )
        assert response.status_code == 422
        assert _risk_event_count_total() == events_before
        assert _user_count_total() == users_before
    finally:
        cleanup_user(long_user_id)


def test_malformed_ip_returns_200_with_invalid_ip_status(user_id: str) -> None:
    """Malformed IPs are scored (200, geo_location_status="invalid_ip"),
    never a 422/500: validation accepts any string and the geo layer
    classifies it. A previous session must exist for the invalid_ip branch
    (a first-ever session short-circuits to "no_history" first)."""
    _seed_previous_session(
        user_id,
        DOMAIN_DEVICE,
        "192.168.1.5",
        datetime.now(timezone.utc) - timedelta(hours=1),
    )
    for bad_ip in ("not-an-ip", "999.999.999.999"):
        response = client.post("/session/score", json=_payload(user_id, ip_address=bad_ip))
        assert response.status_code == 200
        body = response.json()
        signals = body["contributing_signals"]
        assert signals["geo_velocity_kmh"] == 0.0
        assert signals["geo_location_status"] == "invalid_ip"
        _assert_response_matches_persisted(body)


def test_malformed_ip_first_ever_session_is_no_history(user_id: str) -> None:
    """Precedence: with no previous session, the geo layer returns
    "no_history" even for a malformed IP (no_hstory branch runs first)."""
    response = client.post("/session/score", json=_payload(user_id, ip_address="not-an-ip"))
    assert response.status_code == 200
    body = response.json()
    signals = body["contributing_signals"]
    assert signals["geo_velocity_kmh"] == 0.0
    assert signals["geo_location_status"] == "no_history"
    _assert_response_matches_persisted(body)


def test_garbage_refresh_token_no_crash_no_reuse(user_id: str) -> None:
    """A garbage token must not crash and must not register as reuse. A real
    stored hash exists for the previous session, so this also proves the
    non-matching branch (emoji + very long token)."""
    previous_session_id = _seed_previous_session(
        user_id,
        DOMAIN_DEVICE,
        "192.168.1.5",
        datetime.now(timezone.utc) - timedelta(hours=1),
    )
    _store_token_for_session(previous_session_id, "tok-real")
    garbage = "🔥" * 10 + "x" * 2000 + "\n\ttrailing"
    response = client.post(
        "/session/score", json=_payload(user_id, refresh_token=garbage)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["contributing_signals"]["token_reuse_flag"] is False
    _assert_response_matches_persisted(body)


def test_persistence_failure_returns_503_without_internal_details(
    user_id: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Force a real persistence IntegrityError and verify the 503 leaks nothing.

    Mechanism: pin ``uuid4`` to a fixed value and pre-seed a session row with
    that exact session_id, so the live request's session INSERT hits the real
    ``sessions.session_id`` unique constraint. The resulting database error
    (SQL text, constraint name) must stay server-side only: the response body
    is exactly the fixed generic message, nothing is persisted, and the full
    database error is logged.
    """
    pinned_uuid = uuid.uuid4()
    pinned_session_id = str(pinned_uuid)
    _seed_previous_session(
        user_id,
        DOMAIN_DEVICE,
        "192.168.1.5",
        datetime.now(timezone.utc) - timedelta(hours=1),
        session_id=pinned_session_id,
    )
    monkeypatch.setattr(uuid, "uuid4", lambda: pinned_uuid)

    with caplog.at_level("ERROR"):
        response = client.post("/session/score", json=_payload(user_id))

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Scoring service temporarily unavailable. Please try again later."
    }

    # The raw body must not contain SQL statements, table names, column names,
    # constraint names, exception details, or database internals.
    body_text = response.text
    for forbidden in (
        "INSERT",
        "SELECT",
        "users",
        "sessions",
        "risk_events",
        "constraint",
        "UniqueViolation",
        "psycopg2",
        "[SQL",
        "DETAIL",
        "users_user_id_key",
        "sessions_session_id_key",
        "Traceback",
        pinned_session_id,
        user_id,
    ):
        assert forbidden not in body_text, (
            f"response body leaked {forbidden!r}: {body_text}"
        )

    # The real database error must be visible server-side: the meaningful
    # constraint identifier plus a database-error indication (tolerant of
    # exact SQLAlchemy/psycopg2 exception formatting).
    log_text = caplog.text
    assert "sessions_session_id_key" in log_text
    database_error_markers = ("UniqueViolation", "duplicate key", "IntegrityError")
    assert any(marker in log_text for marker in database_error_markers), (
        f"no database-error indication in server log: {log_text!r}"
    )

    # Nothing persisted by the failed request: only the seeded session row.
    assert _session_count(user_id) == 1
    assert _risk_event_count_for_user(user_id) == 0


# ---------------------------------------------------------------------------
# Concurrency (real HTTP against the running in-container uvicorn)
# ---------------------------------------------------------------------------


def _fire_concurrent(
    user_id: str, count: int, device_fingerprint: str
) -> list[tuple[str, dict | None]]:
    """Fire `count` simultaneous real-HTTP requests for the same user.

    Threads synchronize on a Barrier so the requests reach the running
    uvicorn (port 8000 inside the api container) at the same moment. Returns
    (status, body) tuples; client-side exceptions are returned as
    ("EXC", {"error": repr(exc)}) so they surface as explicit failures.
    """
    barrier = threading.Barrier(count, timeout=30)

    def _one(_: int) -> tuple[str, dict | None]:
        try:
            barrier.wait(timeout=30)
            response = httpx.post(
                f"{API_BASE_URL}/session/score",
                json=_payload(user_id, device_fingerprint=device_fingerprint),
                timeout=30,
            )
            return str(response.status_code), response.json()
        except Exception as exc:  # surfaced as an explicit test failure
            return "EXC", {"error": repr(exc)}

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(_one, range(count)))


def _assert_concurrent_outcome(
    user_id: str, results: list[tuple[str, dict | None]], expected_events: int
) -> None:
    """Shared success criteria for the concurrency scenarios."""
    statuses = [status for status, _ in results]
    distribution = Counter(statuses)
    assert "EXC" not in statuses, f"client-side errors: {results}"
    assert statuses == ["200"] * len(statuses), (
        f"status distribution: {dict(distribution)}; bodies: {results}"
    )
    bodies = [body for _, body in results if body is not None]
    session_ids = [body["session_id"] for body in bodies]
    assert len(set(session_ids)) == len(session_ids)  # no duplicate sessions
    assert _user_row_count(user_id) == 1  # single user row (find-or-create)
    assert _risk_event_count_for_user(user_id) == expected_events
    for body in bodies:
        burst = body["contributing_signals"]["login_burst_count"]
        # Per-request burst values are not deterministic under true
        # concurrency; bounds are (current attempt included, at most all
        # attempts). The final ZSET state below is the deterministic check.
        assert isinstance(burst, int) and 1 <= burst <= len(bodies)
        _assert_response_matches_persisted(body)
    # All attempts were recorded within the rolling window and every
    # per-request pipeline prunes only members older than 60s, so the final
    # set contains exactly one member per fired request.
    assert _redis.zcard(f"ate:login_burst:{user_id}") == len(bodies)


def test_concurrent_10_requests_fresh_user(user_id: str) -> None:
    """10 simultaneous first-ever requests for a fresh user (race regression lock).

    The find-or-create path is race-safe (INSERT ... ON CONFLICT DO NOTHING +
    re-select), so the full success criteria must hold: every request receives
    200, exactly 10 risk events are persisted, exactly one user row exists,
    session ids are distinct, every response matches its persisted row, and
    ZCARD == 10 (asserted by the shared helper).
    """
    results = _fire_concurrent(user_id, CONCURRENT_REQUESTS, "device-concurrent")
    _assert_concurrent_outcome(
        user_id, results, expected_events=CONCURRENT_REQUESTS
    )


def test_concurrent_10_requests_existing_user(user_id: str) -> None:
    """Concurrent hot path for a pre-created user (no find-or-create race)."""
    with SessionLocal() as db:
        db.add(User(user_id=user_id))
        db.commit()
    results = _fire_concurrent(user_id, CONCURRENT_REQUESTS, "device-concurrent")
    _assert_concurrent_outcome(
        user_id, results, expected_events=CONCURRENT_REQUESTS
    )
