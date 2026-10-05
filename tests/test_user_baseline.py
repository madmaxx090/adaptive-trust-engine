"""Per-user adaptive baseline tests (the additive signal from user_baseline.py).

Runs inside the Docker Compose stack against the real Postgres and Redis
services. Geo velocities are controlled with the documented monkeypatch
mechanism (same pattern as test_endpoint_validation / test_ml_signal) so the
velocity history a user accumulates is exact and the percentile assertions are
deterministic. All state created here is cleaned up per test.

The additive-only guarantee is asserted directly, not assumed: identical frozen
inputs must produce an identical risk score and tier whatever the baseline
reports, because baseline_scorer.py never sees these values.
"""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.session import SERVICE_UNAVAILABLE_DETAIL
from app.core.database import SessionLocal
from app.main import app
from app.models import Session, User
from app.services import geo, risk_pipeline
from app.services.baseline_scorer import score_session as frozen_score_session
from app.services.user_baseline import (
    STATUS_NO_HISTORY,
    STATUS_OK,
    UserBaseline,
    UserBaselineUnavailableError,
)
from tests.test_live_signals import cleanup_user

client = TestClient(app)

DOMAIN_DEVICE = "device-baseline"
BASELINE_KEYS = (
    "user_baseline_status",
    "device_seen_before_count",
    "geo_velocity_user_percentile",
)


@pytest.fixture
def user_id() -> str:
    value = f"test-baseline-{uuid.uuid4().hex[:12]}"
    yield value
    cleanup_user(value)  # teardown runs even when assertions fail


@pytest.fixture
def two_user_ids() -> tuple[str, str]:
    """Two independent users, so both are first-ever (identical frozen inputs)."""
    values = (
        f"test-baseline-{uuid.uuid4().hex[:12]}",
        f"test-baseline-{uuid.uuid4().hex[:12]}",
    )
    yield values
    for value in values:
        cleanup_user(value)


@pytest.fixture
def fake_geo(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Force geo.compute_geo_velocity to a caller-controlled measurement.

    Returned dict is mutated by the test to set the velocity and status of each
    subsequent request; coordinates are fixed so nothing else varies.
    """
    state: dict = {"velocity_kmh": 0.0, "status": STATUS_OK}

    def _fake_compute_geo_velocity(
        previous_ip: str | None,
        previous_last_seen_at,
        current_ip: str,
        current_time,
    ) -> tuple[float, str, tuple[float, float] | None]:
        return state["velocity_kmh"], state["status"], (37.7510, -97.8220)

    monkeypatch.setattr(geo, "compute_geo_velocity", _fake_compute_geo_velocity)
    return state


def _payload(
    user_id: str,
    *,
    ip_address: str = "192.168.1.10",
    device_fingerprint: str = DOMAIN_DEVICE,
) -> dict[str, str]:
    return {
        "user_id": user_id,
        "ip_address": ip_address,
        "device_fingerprint": device_fingerprint,
    }


def _score(user_id: str, **payload_overrides: str) -> dict:
    response = client.post("/session/score", json=_payload(user_id, **payload_overrides))
    assert response.status_code == 200, response.text
    return response.json()


def _signals(body: dict) -> dict:
    return body["contributing_signals"]


def _session_count(user_id: str) -> int:
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(User.user_id == user_id)
        ).scalar_one_or_none()
        if user is None:
            return 0
        return db.execute(
            select(func.count()).select_from(Session).where(Session.user_id == user.id)
        ).scalar_one()


def _stub_baseline(baseline: UserBaseline):
    """Replace the pipeline's baseline call with a fixed return value."""

    def _stub(**_kwargs) -> UserBaseline:
        return baseline

    return _stub


# ---------------------------------------------------------------------------
# Cold start and device history
# ---------------------------------------------------------------------------


def test_cold_start_reports_no_history(user_id: str) -> None:
    """A brand-new user gets the explicit cold-start status, not fake zeros."""
    signals = _signals(_score(user_id))
    assert signals["user_baseline_status"] == STATUS_NO_HISTORY
    assert signals["device_seen_before_count"] == 0
    assert signals["geo_velocity_user_percentile"] is None


def test_same_device_counts_prior_sightings(user_id: str) -> None:
    """Each prior session on this exact fingerprint adds one to the count."""
    assert _signals(_score(user_id))["user_baseline_status"] == STATUS_NO_HISTORY

    second = _signals(_score(user_id))
    assert second["user_baseline_status"] == STATUS_OK
    assert second["device_seen_before_count"] == 1

    third = _signals(_score(user_id))
    assert third["user_baseline_status"] == STATUS_OK
    assert third["device_seen_before_count"] == 2


def test_unseen_device_for_existing_user_counts_zero(user_id: str) -> None:
    """History exists but not for this device: status ok, count 0."""
    _score(user_id)
    signals = _signals(
        _score(user_id, device_fingerprint="device-baseline-unseen")
    )
    assert signals["user_baseline_status"] == STATUS_OK
    assert signals["device_seen_before_count"] == 0


# ---------------------------------------------------------------------------
# Velocity percentile
# ---------------------------------------------------------------------------


def test_percentile_ranks_current_velocity_against_history(
    user_id: str, fake_geo: dict
) -> None:
    """Percentile of the current velocity within the user's measured history.

    Also locks the ordering guarantee: the baseline is computed BEFORE the
    current session is persisted, so a session never contributes to its own
    baseline (the first "ok" request below has an empty history, not [100.0]).
    """
    fake_geo.update(velocity_kmh=100.0, status=STATUS_OK)
    first = _signals(_score(user_id))
    assert first["geo_velocity_user_percentile"] is None  # history still empty

    fake_geo.update(velocity_kmh=300.0, status=STATUS_OK)
    second = _signals(_score(user_id))
    assert second["geo_velocity_user_percentile"] == 100.0  # above [100.0]

    fake_geo.update(velocity_kmh=50.0, status=STATUS_OK)
    third = _signals(_score(user_id))
    assert third["geo_velocity_user_percentile"] == 0.0  # below [100.0, 300.0]

    fake_geo.update(velocity_kmh=200.0, status=STATUS_OK)
    fourth = _signals(_score(user_id))
    # History [100.0, 300.0, 50.0]; 100.0 and 50.0 are at or below 200.0.
    assert fourth["geo_velocity_user_percentile"] == 66.67


def test_history_but_no_measured_velocity_yields_none(
    user_id: str, fake_geo: dict
) -> None:
    """Prior sessions exist but none is a measurement: ok + null percentile.

    Distinct from "no_history" -- the user is known, there is simply nothing
    measured to rank against.
    """
    fake_geo.update(velocity_kmh=0.0, status="private_ip")
    _score(user_id)

    fake_geo.update(velocity_kmh=500.0, status=STATUS_OK)
    signals = _signals(_score(user_id))
    assert signals["user_baseline_status"] == STATUS_OK
    assert signals["geo_velocity_user_percentile"] is None


def test_placeholder_velocities_are_excluded_from_history(
    user_id: str, fake_geo: dict
) -> None:
    """geo.py reports 0.0 when it could not measure; those zeros are not data.

    Counting them would silently drag every percentile towards the middle of
    the range, so both assertions below fail loudly if the status filter is
    ever dropped.
    """
    fake_geo.update(velocity_kmh=0.0, status="private_ip")
    _score(user_id)

    # Were the private_ip placeholder counted, 0.0 <= 500.0 would rank this at
    # the 100th percentile instead of reporting no measured history.
    fake_geo.update(velocity_kmh=500.0, status=STATUS_OK)
    assert _signals(_score(user_id))["geo_velocity_user_percentile"] is None

    fake_geo.update(velocity_kmh=0.0, status="geolocation_not_found")
    _score(user_id)

    # Measured history is only [500.0], so 400.0 sits at the 0th percentile.
    # Counting the two placeholder zeros as well would give 66.67.
    fake_geo.update(velocity_kmh=400.0, status=STATUS_OK)
    assert _signals(_score(user_id))["geo_velocity_user_percentile"] == 0.0


# ---------------------------------------------------------------------------
# Additive-only guarantee and read path
# ---------------------------------------------------------------------------


def test_baseline_never_changes_the_frozen_score(
    monkeypatch: pytest.MonkeyPatch, two_user_ids: tuple[str, str]
) -> None:
    """Identical frozen inputs + very different baselines -> identical decision.

    Both users are first-ever, so the frozen scorer receives the same four
    values; only the stubbed baseline differs. Any change to risk_score or
    risk_tier here would mean the baseline leaked into the frozen scoring path.
    """
    baselines = (
        UserBaseline(
            status=STATUS_NO_HISTORY,
            device_seen_before_count=0,
            geo_velocity_user_percentile=None,
        ),
        UserBaseline(
            status=STATUS_OK,
            device_seen_before_count=99,
            geo_velocity_user_percentile=100.0,
        ),
    )
    bodies = []
    for target_user, baseline in zip(two_user_ids, baselines):
        monkeypatch.setattr(
            risk_pipeline, "compute_user_baseline", _stub_baseline(baseline)
        )
        bodies.append(_score(target_user))

    assert bodies[0]["risk_score"] == bodies[1]["risk_score"]
    assert bodies[0]["risk_tier"] == bodies[1]["risk_tier"]

    for body, baseline in zip(bodies, baselines):
        signals = _signals(body)
        assert signals["user_baseline_status"] == baseline.status
        assert signals["device_seen_before_count"] == baseline.device_seen_before_count
        assert signals["geo_velocity_user_percentile"] == (
            baseline.geo_velocity_user_percentile
        )
        # The decision still equals the frozen scorer applied to the frozen
        # signals alone -- the baseline keys are bystanders.
        raw, tier, _ = frozen_score_session(
            signals["geo_velocity_kmh"],
            signals["device_mismatch_score"],
            signals["token_reuse_flag"],
            signals["login_burst_count"],
        )
        assert body["risk_score"] == int(round(raw))
        assert body["risk_tier"] == tier


def test_baseline_is_persisted_and_readable(user_id: str, fake_geo: dict) -> None:
    """GET /sessions/{id} reports the same baseline POST /session/score did."""
    fake_geo.update(velocity_kmh=120.0, status=STATUS_OK)
    _score(user_id)
    fake_geo.update(velocity_kmh=480.0, status=STATUS_OK)
    scored = _score(user_id)
    assert _signals(scored)["geo_velocity_user_percentile"] == 100.0

    response = client.get(f"/sessions/{scored['session_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["contributing_signals"] == _signals(scored)
    assert body["contributing_signals"]["device_seen_before_count"] == 1

    with SessionLocal() as db:
        session_row = db.execute(
            select(Session).where(Session.session_id == scored["session_id"])
        ).scalar_one()
        assert session_row is not None


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------


def test_baseline_failure_returns_sanitized_503(
    user_id: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A baseline DB failure -> generic 503, detail server-side, nothing stored.

    The baseline runs before persistence, so a failure must leave no partial
    session behind rather than returning a decision without its baseline.
    """
    secret = "SELECT device_fingerprint FROM sessions"

    def _boom(**_kwargs) -> UserBaseline:
        raise UserBaselineUnavailableError(f"simulated baseline outage: {secret}")

    monkeypatch.setattr(risk_pipeline, "compute_user_baseline", _boom)
    with caplog.at_level(logging.ERROR):
        response = client.post("/session/score", json=_payload(user_id))

    assert response.status_code == 503
    assert response.json() == {"detail": SERVICE_UNAVAILABLE_DETAIL}
    assert secret not in response.text
    assert "simulated baseline outage" in caplog.text
    assert _session_count(user_id) == 0
