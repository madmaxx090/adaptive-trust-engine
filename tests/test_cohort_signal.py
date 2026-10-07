"""Cross-account cohort signal tests (the additive signal from cohort_signal.py).

Runs inside the Docker Compose stack against the real Postgres and Redis
services. Every test gets its own device fingerprint and refresh token, so the
cohort sorted sets are isolated by construction and no test can read another
test's members. All IPs are private, so geo short-circuits to a 0.0 velocity
before any GeoLite2 access and every request in a test sees identical frozen
inputs -- which is what makes the additive-only assertions below meaningful.

The additive-only guarantee is asserted twice: once with the cohort signal
stubbed to two extreme values, and once with the real signal producing genuinely
different counts for different requests. Identical frozen inputs must produce an
identical risk score and tier either way, because baseline_scorer.py never sees
these values.
"""

import hashlib
import logging
import time
import uuid

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.session import SERVICE_UNAVAILABLE_DETAIL
from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models import Session, User
from app.services import cohort_signal, risk_pipeline
from app.services.baseline_scorer import score_session as frozen_score_session
from app.services.cohort_signal import (
    COHORT_KEY_TTL_SECONDS,
    COHORT_WINDOW_SECONDS,
    DEVICE_KEY_PREFIX,
    STATUS_OK,
    TOKEN_KEY_PREFIX,
    CohortSignal,
    CohortSignalUnavailableError,
    compute_cohort_signal,
)
from tests.test_live_signals import cleanup_user

client = TestClient(app)

_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def cohort_users():
    """Factory for isolated user ids, each cleaned up on teardown.

    Teardown runs even when an assertion fails, so a partially-run test cannot
    leave its accounts filed under a shared fingerprint.
    """
    created: list[str] = []

    def _make(count: int = 1) -> list[str]:
        values = [f"test-cohort-{uuid.uuid4().hex[:12]}" for _ in range(count)]
        created.extend(values)
        return values

    yield _make
    for value in created:
        cleanup_user(value)


@pytest.fixture
def shared_device() -> str:
    """A fingerprint unique to this test; its cohort key is removed afterwards."""
    value = f"device-cohort-{uuid.uuid4().hex[:12]}"
    yield value
    _redis.delete(f"{DEVICE_KEY_PREFIX}{value}")


@pytest.fixture
def shared_token() -> str:
    """A refresh token unique to this test; its cohort key is removed after."""
    value = f"tok-cohort-{uuid.uuid4().hex[:12]}"
    yield value
    _redis.delete(f"{TOKEN_KEY_PREFIX}{_token_hash(value)}")


def _token_hash(raw_token: str) -> str:
    """Same server-side hash the pipeline uses (only hashes are ever stored)."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _device_key(device_fingerprint: str) -> str:
    return f"{DEVICE_KEY_PREFIX}{device_fingerprint}"


def _token_key(raw_token: str) -> str:
    return f"{TOKEN_KEY_PREFIX}{_token_hash(raw_token)}"


def _score(
    user_id: str,
    *,
    device_fingerprint: str,
    refresh_token: str | None = None,
) -> dict:
    payload: dict[str, str] = {
        "user_id": user_id,
        # Private IP: geo returns 0.0/"private_ip" (or "no_history" on a first
        # session) without touching GeoLite2, so the frozen velocity input is
        # identical for every request in a test.
        "ip_address": "192.168.1.10",
        "device_fingerprint": device_fingerprint,
    }
    if refresh_token is not None:
        payload["refresh_token"] = refresh_token
    response = client.post("/session/score", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _signals(body: dict) -> dict:
    return body["contributing_signals"]


def _cohort_of(body: dict) -> tuple[int, int | None]:
    signals = _signals(body)
    return (
        signals["device_cohort_user_count"],
        signals["token_cohort_user_count"],
    )


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


def _seed_cohort_member(key: str, member: str, age_seconds: float) -> None:
    """File ``member`` under ``key`` as if seen ``age_seconds`` ago."""
    _redis.zadd(key, {member: time.time() - age_seconds})


def _cohort_members(key: str) -> set[str]:
    """Every account currently filed under ``key``."""
    return set(_redis.zrange(key, 0, -1))


def _stub_cohort(cohort: CohortSignal):
    """Replace the pipeline's cohort call with a fixed return value."""

    def _stub(**_kwargs) -> CohortSignal:
        return cohort

    return _stub


class _FailingPipeline:
    """Redis pipeline whose execute() reports a connection failure."""

    def __enter__(self) -> "_FailingPipeline":
        return self

    def __exit__(self, *_exc_info) -> bool:
        return False

    def zremrangebyscore(self, *_args, **_kwargs) -> "_FailingPipeline":
        return self

    def zcard(self, *_args, **_kwargs) -> "_FailingPipeline":
        return self

    def zscore(self, *_args, **_kwargs) -> "_FailingPipeline":
        return self

    def zadd(self, *_args, **_kwargs) -> "_FailingPipeline":
        return self

    def expire(self, *_args, **_kwargs) -> "_FailingPipeline":
        return self

    def execute(self):
        raise redis.ConnectionError("simulated cohort Redis outage")


class _FailingClient:
    def pipeline(self, transaction: bool = False) -> _FailingPipeline:
        return _FailingPipeline()


# ---------------------------------------------------------------------------
# Device cohort: one account vs. several
# ---------------------------------------------------------------------------


def test_single_account_on_a_device_counts_one(cohort_users, shared_device) -> None:
    """A device used by one account reports 1 -- the not-shared baseline.

    The count includes the current request, so 1 means "this account alone"
    rather than 0. Reporting 0 for a lone account would make the uninteresting
    case indistinguishable from "the signal did not run".
    """
    (user,) = cohort_users(1)
    device_count, token_count = _cohort_of(
        _score(user, device_fingerprint=shared_device)
    )
    assert device_count == 1
    assert token_count is None  # no refresh token presented
    assert _redis.zcard(_device_key(shared_device)) == 1
    assert _cohort_members(_device_key(shared_device)) == {user}


def test_returning_account_does_not_inflate_its_own_cohort(
    cohort_users, shared_device
) -> None:
    """Repeat visits from one account keep the count at 1 (regression lock).

    The signal reads the set before recording the request, and the caller's own
    earlier membership is subtracted before the one is added back. Without that
    correction a lone account's second request would read 1 and look shared.
    """
    (user,) = cohort_users(1)
    for _ in range(3):
        assert _cohort_of(_score(user, device_fingerprint=shared_device))[0] == 1
    assert _redis.zcard(_device_key(shared_device)) == 1


def test_distinct_accounts_sharing_a_device_count_up(
    cohort_users, shared_device
) -> None:
    """Three accounts on one fingerprint inside the window report 1, 2, 3.

    This is the credential-stuffing shape: each request looks perfectly normal
    per-user (first session, no device change, no burst), and only the sideways
    view across accounts reveals that one device is driving many logins.
    """
    users = cohort_users(3)
    reported = [
        _cohort_of(_score(user, device_fingerprint=shared_device))[0] for user in users
    ]
    assert reported == [1, 2, 3]
    key = _device_key(shared_device)
    assert _redis.zcard(key) == 3
    assert _cohort_members(key) == set(users)


def test_device_cohort_is_scoped_to_the_fingerprint(
    cohort_users, shared_device
) -> None:
    """A second fingerprint for the same account starts its own count at 1."""
    (user,) = cohort_users(1)
    assert _cohort_of(_score(user, device_fingerprint=shared_device))[0] == 1
    other_device = f"{shared_device}-other"
    try:
        assert _cohort_of(_score(user, device_fingerprint=other_device))[0] == 1
        assert _redis.zcard(_device_key(other_device)) == 1
    finally:
        _redis.delete(_device_key(other_device))


# ---------------------------------------------------------------------------
# Token-hash cohort
# ---------------------------------------------------------------------------


def test_single_account_token_hash_counts_one(
    cohort_users, shared_device, shared_token
) -> None:
    """One refresh token under one account reports 1 -- the normal case."""
    (user,) = cohort_users(1)
    device_count, token_count = _cohort_of(
        _score(user, device_fingerprint=shared_device, refresh_token=shared_token)
    )
    assert device_count == 1
    assert token_count == 1
    assert _cohort_members(_token_key(shared_token)) == {user}


def test_distinct_accounts_sharing_a_token_hash_count_up(
    cohort_users, shared_token
) -> None:
    """One token presented by three accounts on three DIFFERENT devices.

    The device cohort stays at 1 for every request while the token cohort climbs
    to 3, which is exactly why the two dimensions are tracked separately: this
    pattern is invisible to any device-based check.
    """
    users = cohort_users(3)
    devices = [f"device-cohort-token-{uuid.uuid4().hex[:8]}-{i}" for i in range(3)]
    try:
        reported = [
            _cohort_of(
                _score(user, device_fingerprint=device, refresh_token=shared_token)
            )
            for user, device in zip(users, devices)
        ]
        assert [device_count for device_count, _ in reported] == [1, 1, 1]
        assert [token_count for _, token_count in reported] == [1, 2, 3]
        assert _redis.zcard(_token_key(shared_token)) == 3
    finally:
        for device in devices:
            _redis.delete(_device_key(device))


def test_missing_refresh_token_leaves_the_token_cohort_null(
    cohort_users, shared_device
) -> None:
    """No token was presented, so nothing is observable: null, not a fake 1.

    A fabricated count would claim the signal ran on data that does not exist,
    which is the same distinction the baseline draws between "no_history" and a
    genuine zero.
    """
    (user,) = cohort_users(1)
    assert _cohort_of(_score(user, device_fingerprint=shared_device))[1] is None


def test_only_the_token_hash_is_used_as_a_key(
    cohort_users, shared_device, shared_token
) -> None:
    """The raw refresh token never reaches Redis (same rule as session_store)."""
    (user,) = cohort_users(1)
    _score(user, device_fingerprint=shared_device, refresh_token=shared_token)

    assert _redis.exists(_token_key(shared_token)) == 1
    assert list(_redis.scan_iter(match=f"*{shared_token}*")) == []
    # The members are account ids, not tokens.
    assert _cohort_members(_token_key(shared_token)) == {user}


# ---------------------------------------------------------------------------
# 24-hour rolling window and key TTL
# ---------------------------------------------------------------------------


def test_members_outside_the_window_are_pruned(cohort_users, shared_device) -> None:
    """A member older than 24h is dropped, so it cannot inflate the count.

    This is the reason a sorted set is used rather than a plain SET: each member
    carries its own observation time and ages out individually.
    """
    (user,) = cohort_users(1)
    stale = f"stale-{uuid.uuid4().hex[:12]}"
    key = _device_key(shared_device)
    _seed_cohort_member(key, stale, COHORT_WINDOW_SECONDS + 3600)
    assert _redis.zcard(key) == 1

    assert _cohort_of(_score(user, device_fingerprint=shared_device))[0] == 1
    assert _redis.zscore(key, stale) is None  # pruned, not merely ignored
    assert _cohort_members(key) == {user}


def test_members_inside_the_window_still_count(cohort_users, shared_device) -> None:
    """A member seen 23 hours ago is still a live cohort peer."""
    (user,) = cohort_users(1)
    recent = f"recent-{uuid.uuid4().hex[:12]}"
    key = _device_key(shared_device)
    _seed_cohort_member(key, recent, COHORT_WINDOW_SECONDS - 3600)

    assert _cohort_of(_score(user, device_fingerprint=shared_device))[0] == 2
    assert _cohort_members(key) == {user, recent}


def test_key_ttl_is_refreshed_to_the_window(cohort_users, shared_device) -> None:
    """Each write resets the key TTL to the window length.

    The TTL only reclaims keys that have gone quiet for a full window; members
    inside a still-active key are aged out by the score prune above, not by the
    TTL.
    """
    (user,) = cohort_users(1)
    key = _device_key(shared_device)
    _score(user, device_fingerprint=shared_device)
    ttl = _redis.ttl(key)
    assert COHORT_KEY_TTL_SECONDS - 60 < ttl <= COHORT_KEY_TTL_SECONDS


# ---------------------------------------------------------------------------
# Additive-only guarantee
# ---------------------------------------------------------------------------


def test_cohort_never_changes_the_frozen_score(
    monkeypatch: pytest.MonkeyPatch, cohort_users, shared_device
) -> None:
    """Identical frozen inputs + very different cohorts -> identical decision.

    Both accounts are first-ever on a private IP, so the frozen scorer receives
    the same four values; only the stubbed cohort differs, by four orders of
    magnitude. Any change to risk_score or risk_tier here would mean the cohort
    signal leaked into the frozen scoring path.
    """
    cohorts = (
        CohortSignal(
            status=STATUS_OK, device_cohort_user_count=1, token_cohort_user_count=None
        ),
        CohortSignal(
            status=STATUS_OK,
            device_cohort_user_count=5000,
            token_cohort_user_count=5000,
        ),
    )
    bodies = []
    for target_user, cohort in zip(cohort_users(2), cohorts):
        monkeypatch.setattr(
            risk_pipeline, "compute_cohort_signal", _stub_cohort(cohort)
        )
        bodies.append(_score(target_user, device_fingerprint=shared_device))

    assert bodies[0]["risk_score"] == bodies[1]["risk_score"]
    assert bodies[0]["risk_tier"] == bodies[1]["risk_tier"]

    for body, cohort in zip(bodies, cohorts):
        assert _cohort_of(body) == (
            cohort.device_cohort_user_count,
            cohort.token_cohort_user_count,
        )
        signals = _signals(body)
        # The decision still equals the frozen scorer applied to the frozen
        # signals alone -- the cohort keys are bystanders.
        raw, tier, _ = frozen_score_session(
            signals["geo_velocity_kmh"],
            signals["device_mismatch_score"],
            signals["token_reuse_flag"],
            signals["login_burst_count"],
        )
        assert body["risk_score"] == int(round(raw))
        assert body["risk_tier"] == tier


def test_real_shared_device_does_not_change_the_frozen_score(
    cohort_users, shared_device
) -> None:
    """The same guarantee with the real signal: counts differ, decisions do not.

    Stronger than the stubbed version because nothing is monkeypatched -- the
    live cohort values genuinely diverge (1, 2, 3) across the three requests
    while every frozen input stays identical.
    """
    users = cohort_users(3)
    bodies = [_score(user, device_fingerprint=shared_device) for user in users]

    device_counts = [
        body["contributing_signals"]["device_cohort_user_count"] for body in bodies
    ]
    assert device_counts == [1, 2, 3]
    assert len({(body["risk_score"], body["risk_tier"]) for body in bodies}) == 1
    for body in bodies:
        signals = _signals(body)
        raw, tier, _ = frozen_score_session(
            signals["geo_velocity_kmh"],
            signals["device_mismatch_score"],
            signals["token_reuse_flag"],
            signals["login_burst_count"],
        )
        assert body["risk_score"] == int(round(raw))
        assert body["risk_tier"] == tier


# ---------------------------------------------------------------------------
# Read path and failure path
# ---------------------------------------------------------------------------


def test_signal_reports_ok_status(
    cohort_users, shared_device, shared_token
) -> None:
    """The dataclass contract: status "ok", and None (not 0) without a token."""
    (user,) = cohort_users(1)
    signal = compute_cohort_signal(
        user_id=user,
        device_fingerprint=shared_device,
        token_hash=_token_hash(shared_token),
    )
    assert signal.status == STATUS_OK
    assert signal.device_cohort_user_count == 1
    assert signal.token_cohort_user_count == 1

    no_token = compute_cohort_signal(
        user_id=user, device_fingerprint=shared_device, token_hash=None
    )
    assert no_token.token_cohort_user_count is None


def test_cohort_is_persisted_and_readable(
    cohort_users, shared_device, shared_token
) -> None:
    """GET /sessions/{id} reports the same cohort counts POST returned."""
    users = cohort_users(2)
    scored = [
        _score(user, device_fingerprint=shared_device, refresh_token=shared_token)
        for user in users
    ]
    device_counts = [
        body["contributing_signals"]["device_cohort_user_count"] for body in scored
    ]
    assert device_counts == [1, 2]

    response = client.get(f"/sessions/{scored[1]['session_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["contributing_signals"] == _signals(scored[1])
    assert body["contributing_signals"]["device_cohort_user_count"] == 2
    assert body["contributing_signals"]["token_cohort_user_count"] == 2


def test_redis_outage_returns_sanitized_503(
    cohort_users,
    shared_device,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cohort Redis failure -> generic 503, detail server-side, nothing stored.

    Only cohort_signal._client is replaced, so the burst counter and the
    token-reuse check still talk to the real Redis: the failure is isolated to
    this additive step. The cohort runs before persistence, so it must leave no
    partial session behind rather than return a decision without its cohort.
    """
    secret = "ate:cohort:device:internal-key-name"

    class _LeakyPipeline(_FailingPipeline):
        def execute(self):
            raise redis.ConnectionError(f"simulated cohort Redis outage: {secret}")

    class _LeakyClient:
        def pipeline(self, transaction: bool = False) -> _LeakyPipeline:
            return _LeakyPipeline()

    monkeypatch.setattr(cohort_signal, "_client", _LeakyClient())
    (user,) = cohort_users(1)
    with caplog.at_level(logging.ERROR):
        response = client.post(
            "/session/score",
            json={
                "user_id": user,
                "ip_address": "192.168.1.10",
                "device_fingerprint": shared_device,
            },
        )

    assert response.status_code == 503
    assert response.json() == {"detail": SERVICE_UNAVAILABLE_DETAIL}
    assert secret not in response.text
    assert "simulated cohort Redis outage" in caplog.text
    assert _session_count(user) == 0


def test_redis_outage_raises_the_dedicated_error(
    cohort_users, shared_device, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The module raises its own exception type, chained to the Redis error."""
    monkeypatch.setattr(cohort_signal, "_client", _FailingClient())
    (user,) = cohort_users(1)
    with pytest.raises(CohortSignalUnavailableError) as excinfo:
        compute_cohort_signal(
            user_id=user, device_fingerprint=shared_device, token_hash=None
        )
    assert "simulated cohort Redis outage" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, redis.RedisError)
