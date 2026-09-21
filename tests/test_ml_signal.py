"""Live ML signal integration tests (Isolation Forest alongside the baseline).

Runs inside the Docker Compose stack against the real Postgres and Redis
services; geo velocities are controlled with the documented monkeypatch
mechanism (same pattern as test_endpoint_validation). All state created here
is cleaned up per test.

Measured expectations: every decision-score expectation below was measured
from the persisted artifact (ml_model/isolation_forest_v1.joblib, built by
train_ml_model.py and gated against the frozen ml_results.json). Inference is
deterministic -- random_state affects fitting only -- so identical inputs
must always reproduce identical outputs.
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models import Session, User
from app.services import geo, ml_runtime, risk_pipeline
from app.services.baseline_scorer import score_session as frozen_score_session
from app.services.isolation_forest_scorer import feature_row, to_project_predictions
from app.services.ml_runtime import MLSignalError, IsolationForestRuntime
from app.services.session_store import store_refresh_token_hash
from tests.test_live_signals import cleanup_user

client = TestClient(app)

_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)

# ---------------------------------------------------------------------------
# Measured from the persisted artifact (see module docstring). The 1e-9
# tolerance is effectively exact: these values are deterministic.
# ---------------------------------------------------------------------------

CLEAN_ROW = (100.0, 0.0, False, 1)
CLEAN_DF = 0.0062293055959755095

FIRST_SESSION_ROW = (0.0, 0.0, False, 1)
FIRST_SESSION_DF = -0.012665046501311339

IMPOSSIBLE_TRAVEL_ROW = (9000.0, 0.0, False, 1)
IMPOSSIBLE_TRAVEL_DF = -0.26595221861946344

DEVICE_CHANGE_ROW = (100.0, 1.0, False, 1)
DEVICE_CHANGE_DF = -0.2225610373474594

TOKEN_REUSE_ROW = (100.0, 0.0, True, 1)
TOKEN_REUSE_DF = -0.2356360696628929

BURST_ROW = (100.0, 0.0, False, 30)
BURST_DF = -0.2569806173893823

COMBINED_ROW = (600.0, 1.0, True, 3)
COMBINED_DF = -0.2816215936804591


@pytest.fixture
def user_id() -> str:
    value = f"test-ml-{uuid.uuid4().hex[:12]}"
    yield value
    cleanup_user(value)


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
) -> str:
    """Insert the user (if absent) plus one previous session row.

    Returns the previous session's external session_id (needed to seed a
    stored refresh-token hash for the token-reuse scenario).
    """
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(User.user_id == user_id)
        ).scalar_one_or_none()
        if user is None:
            user = User(user_id=user_id)
            db.add(user)
            db.flush()
        session_row = Session(
            session_id=str(uuid.uuid4()),
            user_id=user.id,
            device_fingerprint=device_fingerprint,
            ip_address=ip_address,
            created_at=last_seen_at,
            last_seen_at=last_seen_at,
        )
        db.add(session_row)
        db.commit()
        return session_row.session_id


def _store_previous_token(session_id: str, raw_token: str) -> None:
    store_refresh_token_hash(
        session_id, hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    )


def _seed_login_attempts(user_id: str, count: int) -> None:
    """Seed ``count`` earlier attempts inside the current burst window, so the
    request's own attempt becomes attempt number ``count + 1``."""
    now = time.time()
    mapping = {f"seed:{uuid.uuid4().hex}": now for _ in range(count)}
    key = f"ate:login_burst:{user_id}"
    _redis.zadd(key, mapping)
    _redis.expire(key, 120)


def _install_fake_geo(monkeypatch: pytest.MonkeyPatch, velocity_kmh: float) -> None:
    """Control the live geo-velocity computation (documented test mechanism)."""

    def _fake_compute_geo_velocity(
        previous_ip, previous_last_seen_at, current_ip, current_time
    ):
        return velocity_kmh, "ok", (37.7510, -97.8220)

    monkeypatch.setattr(geo, "compute_geo_velocity", _fake_compute_geo_velocity)


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


# ---------------------------------------------------------------------------
# Runtime-level checks (feature contract, determinism, loading, errors)
# ---------------------------------------------------------------------------


def test_runtime_matches_frozen_helper_pipeline() -> None:
    """The runtime must build its input row with the frozen feature_row (same
    order/encoding as the Phase 5 training code); outputs must equal a direct
    frozen-helper computation on the loaded model."""
    runtime = IsolationForestRuntime()
    runtime.ensure_loaded()
    model = runtime._model
    for row in (CLEAN_ROW, DEVICE_CHANGE_ROW, IMPOSSIBLE_TRAVEL_ROW):
        flag, decision = runtime.score(*row)
        features = feature_row(row[0], row[1], row[2], row[3])
        assert features == [row[0], row[1], 1.0 if row[2] else 0.0, float(row[3])]
        assert flag == bool(to_project_predictions(model.predict([features]))[0])
        assert decision == pytest.approx(
            float(model.decision_function([features])[0]), rel=0, abs=1e-12
        )


def test_ml_inference_is_deterministic() -> None:
    """random_state governs fitting only; at inference the same four features
    must always reproduce the same flag and decision score."""
    runtime = IsolationForestRuntime()
    first = runtime.score(*CLEAN_ROW)
    for _ in range(4):
        assert runtime.score(*CLEAN_ROW) == first
    assert first[0] is False


def test_model_artifact_is_loaded_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The artifact is loaded once per runtime (idempotent load; the lazy
    first-use path loads exactly one time), never per request."""
    load_calls: list[str] = []
    real_load = ml_runtime.joblib.load

    def _counting_load(path):
        load_calls.append(str(path))
        return real_load(path)

    monkeypatch.setattr(ml_runtime.joblib, "load", _counting_load)
    runtime = IsolationForestRuntime()
    runtime.load(settings.ml_model_path)
    runtime.load(settings.ml_model_path)  # idempotent
    runtime.score(*CLEAN_ROW)
    runtime.score(*FIRST_SESSION_ROW)
    assert load_calls == [settings.ml_model_path]


@pytest.mark.parametrize(
    ("row", "expected_message_part"),
    [
        (
            (float("nan"), 0.0, False, 1),
            "Non-finite feature value for geo_velocity_kmh",
        ),
        (
            (float("inf"), 0.0, False, 1),
            "Non-finite feature value for geo_velocity_kmh",
        ),
        (
            (0.0, float("-inf"), False, 1),
            "Non-finite feature value for device_mismatch_score",
        ),
        ((None, 0.0, False, 1), "Non-numeric feature value for geo_velocity_kmh"),
        ((0.0, 0.0, "yes", 1), "token_reuse_flag must be a bool"),
    ],
)
def test_invalid_feature_values_raise_descriptively(
    row: tuple, expected_message_part: str
) -> None:
    """Invalid values reaching the model layer must raise a clear
    MLSignalError (mapped to the sanitized 503 at the API), not crash."""
    runtime = IsolationForestRuntime()
    with pytest.raises(MLSignalError) as excinfo:
        runtime.score(*row)
    assert expected_message_part in str(excinfo.value)
    assert runtime._model is None  # validation happens before any loading


def test_missing_model_artifact_fails_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing artifact must fail application startup (the real lifespan
    context) with a descriptive error naming the path -- not a silent
    fallback pretending the ML signal ran."""
    monkeypatch.setattr(ml_runtime.ml_runtime, "_model", None)
    monkeypatch.setattr(ml_runtime.ml_runtime, "_path", None)
    monkeypatch.setattr(settings, "ml_model_path", "ml_model/does-not-exist.joblib")

    async def _enter_lifespan() -> None:
        async with app.router.lifespan_context(app):
            pass

    with pytest.raises(MLSignalError) as excinfo:
        asyncio.run(_enter_lifespan())
    assert "does-not-exist.joblib" in str(excinfo.value)


def test_corrupt_model_artifact_rejected(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.joblib"
    corrupt.write_bytes(b"this is not a joblib artifact")
    runtime = IsolationForestRuntime()
    with pytest.raises(MLSignalError) as excinfo:
        runtime.load(str(corrupt))
    assert "corrupt.joblib" in str(excinfo.value)
    assert runtime._model is None  # failed load leaves no half-loaded state


def test_lifespan_loads_model_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """The FastAPI lifespan must load the artifact at application startup."""
    monkeypatch.setattr(ml_runtime.ml_runtime, "_model", None)
    monkeypatch.setattr(ml_runtime.ml_runtime, "_path", None)
    with TestClient(app) as startup_client:
        assert ml_runtime.ml_runtime._model is not None
        assert startup_client.get("/health").status_code == 200


def test_artifact_matches_host_verification_record() -> None:
    """Cross-environment check: the containerized artifact must byte-match the
    record written on the host and reproduce its golden inference outputs."""
    model_path = Path(settings.ml_model_path)
    verification = json.loads(
        model_path.with_name("isolation_forest_v1_verification.json").read_text(
            encoding="utf-8"
        )
    )
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest().upper()
    assert digest == verification["model_sha256"]

    import sklearn

    assert sklearn.__version__ == verification["scikit_learn_version"]

    runtime = IsolationForestRuntime()
    for sample in verification["golden_samples"]:
        geo_kmh, device_mismatch, token_flag, burst = sample["features"]
        flag, decision = runtime.score(
            geo_kmh, device_mismatch, bool(token_flag), int(burst)
        )
        assert flag == (sample["predict"] == -1)
        assert flag == bool(sample["flag"])
        assert decision == pytest.approx(sample["decision_score"], rel=0, abs=1e-9)


def test_ml_inference_latency() -> None:
    """Basic latency check: inference should be milliseconds; the measured
    mean is printed for the phase report."""
    runtime = IsolationForestRuntime()
    runtime.ensure_loaded()
    iterations = 100
    start = time.perf_counter()
    for _ in range(iterations):
        runtime.score(250.0, 0.0, False, 1)
    mean_ms = (time.perf_counter() - start) / iterations * 1000.0
    print(
        f"\nMEASURED: mean ml_runtime.score() latency = {mean_ms:.4f} ms "
        f"over {iterations} calls"
    )
    assert mean_ms < 50.0


# ---------------------------------------------------------------------------
# Live endpoint scenarios (HTTP through the real pipeline)
# ---------------------------------------------------------------------------


def test_normal_low_risk_session_both_signals_low(
    user_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 1: a returning user with an in-range velocity is low risk for
    BOTH signals (baseline low tier; ML inlier with a positive score)."""
    _seed_previous_session(
        user_id, "device-A", "8.8.8.8", datetime.now(timezone.utc) - timedelta(hours=1)
    )
    _install_fake_geo(monkeypatch, 100.0)
    response = client.post(
        "/session/score",
        json=_payload(user_id, ip_address="1.1.1.1", device_fingerprint="device-A"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["contributing_signals"] == {
        "geo_velocity_kmh": 100.0,
        "geo_location_status": "ok",
        "device_mismatch_score": 0.0,
        "token_reuse_flag": False,
        "login_burst_count": 1,
    }
    assert body["risk_tier"] == "low"
    _assert_score_consistent(body)
    assert body["ml_anomaly_flag"] is False
    assert body["ml_decision_score"] == pytest.approx(CLEAN_DF, rel=0, abs=1e-9)


def test_first_ever_session_ml_signal_measured_behaviour(user_id: str) -> None:
    """Known live-vs-offline characteristic, locked as measured (F4).

    A first-ever session reports geo_velocity 0.0 ("no_history"), which lies
    BELOW the offline training range for normal sessions (0.98..898.94 km/h);
    the model therefore flags it as a *marginal* anomaly (decision score just
    below zero). This is documented, measured behaviour of the validated
    artifact -- not silently "fixed" by altering the frozen feature contract.
    """
    response = client.post("/session/score", json=_payload(user_id))
    assert response.status_code == 200
    body = response.json()
    assert body["contributing_signals"]["geo_location_status"] == "no_history"
    assert body["risk_tier"] == "low"
    _assert_score_consistent(body)
    assert body["ml_anomaly_flag"] is True
    assert body["ml_decision_score"] == pytest.approx(
        FIRST_SESSION_DF, rel=0, abs=1e-9
    )
    assert body["ml_decision_score"] > -0.05  # marginal, not a strong anomaly


def test_attack_pattern_impossible_travel_flagged(
    user_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 2a: an impossible-travel velocity (9000 km/h) is strongly
    flagged by the ML signal."""
    _seed_previous_session(
        user_id, "device-A", "8.8.8.8", datetime.now(timezone.utc) - timedelta(hours=1)
    )
    _install_fake_geo(monkeypatch, 9000.0)
    response = client.post(
        "/session/score",
        json=_payload(user_id, ip_address="1.1.1.1", device_fingerprint="device-A"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["contributing_signals"]["geo_velocity_kmh"] == 9000.0
    _assert_score_consistent(body)
    assert body["ml_anomaly_flag"] is True
    assert body["ml_decision_score"] == pytest.approx(
        IMPOSSIBLE_TRAVEL_DF, rel=0, abs=1e-9
    )
    assert body["ml_decision_score"] < -0.2  # strong anomaly, not marginal


def test_attack_pattern_device_change_flagged(
    user_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 2b: a device change with otherwise plausible signals is
    strongly flagged by the ML signal."""
    _seed_previous_session(
        user_id, "device-A", "8.8.8.8", datetime.now(timezone.utc) - timedelta(hours=1)
    )
    _install_fake_geo(monkeypatch, 100.0)
    response = client.post(
        "/session/score",
        json=_payload(user_id, ip_address="1.1.1.1", device_fingerprint="device-B"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["contributing_signals"]["device_mismatch_score"] == 1.0
    _assert_score_consistent(body)
    assert body["ml_anomaly_flag"] is True
    assert body["ml_decision_score"] == pytest.approx(
        DEVICE_CHANGE_DF, rel=0, abs=1e-9
    )
    assert body["ml_decision_score"] < -0.2


def test_ml_failure_returns_503_without_internal_details(
    user_id: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A request-time ML failure -> sanitized 503 with the fixed generic body
    (full detail logged server-side); the ML step runs before persistence, so
    nothing is written."""
    secret = "/secret/path/isolation_forest.joblib"

    def _boom(*args, **kwargs):
        raise MLSignalError(f"simulated ML outage for {secret}")

    monkeypatch.setattr(risk_pipeline.ml_runtime, "score", _boom)
    with caplog.at_level(logging.ERROR):
        response = client.post("/session/score", json=_payload(user_id))
    assert response.status_code == 503
    assert response.json() == {
        "detail": "Scoring service temporarily unavailable. Please try again later."
    }
    assert secret not in response.text
    assert "simulated ML outage" in caplog.text
    assert _session_count(user_id) == 0


def test_score_response_has_exact_expected_keys(user_id: str) -> None:
    """Additive-only contract: the established keys stay unchanged and the
    two new ML keys are the only additions."""
    response = client.post("/session/score", json=_payload(user_id))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "risk_score",
        "risk_tier",
        "contributing_signals",
        "session_id",
        "ml_anomaly_flag",
        "ml_decision_score",
    }
    assert set(body["contributing_signals"]) == {
        "geo_velocity_kmh",
        "geo_location_status",
        "device_mismatch_score",
        "token_reuse_flag",
        "login_burst_count",
    }
    assert isinstance(body["ml_anomaly_flag"], bool)
    assert isinstance(body["ml_decision_score"], float)


def test_live_side_by_side_baseline_vs_ml_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live baseline-vs-ML comparison over a small request set (not a re-run
    of the offline Phase 6 comparison): confirms the integration produces
    measured, non-contradictory signals -- every attack-like scenario is
    flagged by the ML signal with a strongly negative decision score, the
    clean scenario is not flagged, and the baseline fields stay internally
    consistent. The printed table is reported as evidence."""
    previous_time = datetime.now(timezone.utc) - timedelta(hours=1)
    scenarios = [
        (
            "clean returning (100 km/h)",
            "device-A",
            100.0,
            False,
            0,
            CLEAN_ROW,
            False,
            CLEAN_DF,
        ),
        (
            "impossible travel (9000 km/h)",
            "device-A",
            9000.0,
            False,
            0,
            IMPOSSIBLE_TRAVEL_ROW,
            True,
            IMPOSSIBLE_TRAVEL_DF,
        ),
        (
            "device change only",
            "device-B",
            100.0,
            False,
            0,
            DEVICE_CHANGE_ROW,
            True,
            DEVICE_CHANGE_DF,
        ),
        (
            "token reuse only",
            "device-A",
            100.0,
            True,
            0,
            TOKEN_REUSE_ROW,
            True,
            TOKEN_REUSE_DF,
        ),
        (
            "burst only (30 attempts)",
            "device-A",
            100.0,
            False,
            29,
            BURST_ROW,
            True,
            BURST_DF,
        ),
        (
            "combined attack (+600 km/h, device, token, burst)",
            "device-B",
            600.0,
            True,
            2,
            COMBINED_ROW,
            True,
            COMBINED_DF,
        ),
    ]

    table_rows: list[tuple[str, int, str, bool, float]] = []
    for (
        name,
        device_now,
        velocity,
        token_reuse,
        burst_seed,
        expected_row,
        expected_flag,
        expected_df,
    ) in scenarios:
        scenario_user = f"test-ml-side-{uuid.uuid4().hex[:12]}"
        try:
            previous_session_id = _seed_previous_session(
                scenario_user, "device-A", "8.8.8.8", previous_time
            )
            refresh_token = None
            if token_reuse:
                refresh_token = "tok-side-by-side"
                _store_previous_token(previous_session_id, refresh_token)
            if burst_seed:
                _seed_login_attempts(scenario_user, burst_seed)
            _install_fake_geo(monkeypatch, velocity)
            response = client.post(
                "/session/score",
                json=_payload(
                    scenario_user,
                    ip_address="1.1.1.1",
                    device_fingerprint=device_now,
                    refresh_token=refresh_token,
                ),
            )
            assert response.status_code == 200, name
            body = response.json()
            signals = body["contributing_signals"]
            received_row = (
                signals["geo_velocity_kmh"],
                signals["device_mismatch_score"],
                signals["token_reuse_flag"],
                signals["login_burst_count"],
            )
            assert received_row == expected_row, name
            _assert_score_consistent(body)
            assert body["ml_anomaly_flag"] is expected_flag, name
            assert body["ml_decision_score"] == pytest.approx(
                expected_df, rel=0, abs=1e-9
            ), name
            table_rows.append(
                (
                    name,
                    body["risk_score"],
                    body["risk_tier"],
                    body["ml_anomaly_flag"],
                    body["ml_decision_score"],
                )
            )
        finally:
            cleanup_user(scenario_user)

    # Ordering sanity: all five attack-like scenarios are strongly flagged;
    # the clean scenario carries a positive (inlier) decision score.
    attack_scores = [row[4] for row in table_rows if row[3]]
    assert len(attack_scores) == 5
    assert all(score < -0.2 for score in attack_scores)
    assert table_rows[0][4] > 0.0

    print("\nBASELINE vs ML (live /session/score):")
    print(f"{'scenario':<52}{'score':>6}{'tier':>8}{'ML flag':>9}{'ML df':>10}")
    for name, risk_score, risk_tier, ml_flag, ml_df in table_rows:
        print(f"{name:<52}{risk_score:>6}{risk_tier:>8}{str(ml_flag):>9}{ml_df:>10.4f}")
