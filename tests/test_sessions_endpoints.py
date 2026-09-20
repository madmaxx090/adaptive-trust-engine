"""Read-only session retrieval endpoints: GET /sessions, GET /sessions/{id}.

Companion to tests/test_live_signals.py and tests/test_endpoint_validation.py,
same hygiene: unique per-test user ids, direct cleanup of Postgres rows and
Redis keys in try/finally, and an independent ORM "mirror" of the list query
used as ground truth (so assertions never depend on unrelated pre-existing
rows in the shared dev database).
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models import RiskEvent, Session, User

client = TestClient(app)

_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)

DOMAIN_DEVICE = "device-A"


# ---------------------------------------------------------------------------
# State helpers (same pattern as tests/test_endpoint_validation.py)
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
    value = f"test-sessions-{uuid.uuid4().hex[:12]}"
    yield value
    cleanup_user(value)  # teardown runs even when assertions fail


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _signals(**overrides: object) -> dict:
    """Persisted contributing_signals shape (Phase-7 pipeline), with overrides."""
    base: dict[str, object] = {
        "geo_velocity_kmh": 0.0,
        "geo_location_status": "no_history",
        "device_mismatch_score": 0.0,
        "token_reuse_flag": False,
        "login_burst_count": 1,
        "risk_score_unrounded": 15.0,  # stored extra key (dropped by responses)
    }
    base.update(overrides)
    return base


def _seed_scored_session(
    user_id: str,
    *,
    created_at: datetime,
    risk_score: int,
    risk_tier: str,
    device_fingerprint: str = DOMAIN_DEVICE,
    ip_address: str = "192.168.1.10",
    signals: dict | None = None,
) -> str:
    """Create user (if needed) + one session + one risk event; return session_id."""
    session_external_id = str(uuid.uuid4())
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(User.user_id == user_id)
        ).scalar_one_or_none()
        if user is None:
            user = User(user_id=user_id)
            db.add(user)
            db.flush()
        session_row = Session(
            session_id=session_external_id,
            user_id=user.id,
            device_fingerprint=device_fingerprint,
            ip_address=ip_address,
            created_at=created_at,
            last_seen_at=created_at,
        )
        db.add(session_row)
        db.flush()
        db.add(
            RiskEvent(
                session_id=session_row.id,
                risk_score=risk_score,
                risk_tier=risk_tier,
                contributing_signals=(
                    signals
                    if signals is not None
                    else _signals(risk_score_unrounded=float(risk_score))
                ),
                created_at=created_at,
            )
        )
        db.commit()
    return session_external_id


# ---------------------------------------------------------------------------
# Ground truth: independent ORM mirror of the list endpoint
# ---------------------------------------------------------------------------


def _expected_sessions(risk_tier: str | None = None) -> list[dict]:
    """Mirror of GET /sessions semantics, computed via plain ORM queries.

    Picks each session's latest risk event by (created_at, id), skips
    event-less sessions (excluded by design), applies the tier filter, and
    orders like the endpoint: created_at desc with the session UUID as the
    deterministic tie-break (stable two-step sort).
    """
    expected: list[dict] = []
    with SessionLocal() as db:
        sessions = db.execute(select(Session)).scalars().all()
        for session_row in sessions:
            latest = db.execute(
                select(RiskEvent)
                .where(RiskEvent.session_id == session_row.id)
                .order_by(RiskEvent.created_at.desc(), RiskEvent.id)
                .limit(1)
            ).scalar_one_or_none()
            if latest is None:
                continue
            if risk_tier is not None and latest.risk_tier != risk_tier:
                continue
            user = db.execute(
                select(User).where(User.id == session_row.user_id)
            ).scalar_one()
            expected.append(
                {
                    "session_id": session_row.session_id,
                    "user_id": user.user_id,
                    "risk_score": latest.risk_score,
                    "risk_tier": latest.risk_tier,
                    "device_fingerprint": session_row.device_fingerprint,
                    "ip_address": session_row.ip_address,
                    "timestamp": session_row.created_at,
                    "_pk": session_row.id,
                }
            )
    expected.sort(key=lambda item: item["_pk"])
    expected.sort(key=lambda item: item["timestamp"], reverse=True)
    return expected


def _collect_all_sessions(
    *, risk_tier: str | None = None
) -> tuple[int, list[dict]]:
    """Page through GET /sessions (limit=100) until exhausted -> (total, items)."""
    items: list[dict] = []
    page = 1
    while True:
        params: dict[str, object] = {"page": page, "limit": 100}
        if risk_tier is not None:
            params["risk_tier"] = risk_tier
        response = client.get("/sessions", params=params)
        assert response.status_code == 200
        body = response.json()
        items.extend(body["sessions"])
        if len(items) >= body["total"] or not body["sessions"]:
            return body["total"], items
        page += 1
        assert page < 500, "pagination did not converge"


def _assert_item_matches(item: dict, expected: dict) -> None:
    assert item["session_id"] == expected["session_id"]
    assert item["user_id"] == expected["user_id"]
    assert item["risk_score"] == expected["risk_score"]
    assert item["risk_tier"] == expected["risk_tier"]
    assert item["device_fingerprint"] == expected["device_fingerprint"]
    assert item["ip_address"] == expected["ip_address"]
    assert datetime.fromisoformat(item["timestamp"]) == expected["timestamp"]


# ---------------------------------------------------------------------------
# GET /sessions
# ---------------------------------------------------------------------------


def test_list_sessions_ground_truth_and_shape(user_id: str) -> None:
    base = datetime.now(timezone.utc) - timedelta(minutes=30)
    seeded = {
        _seed_scored_session(
            user_id,
            created_at=base,
            risk_score=42,
            risk_tier="medium",
            ip_address="192.168.1.50",
        ): {"risk_score": 42, "risk_tier": "medium", "created_at": base},
        _seed_scored_session(
            user_id,
            created_at=base + timedelta(minutes=1),
            risk_score=7,
            risk_tier="low",
            ip_address="192.168.1.51",
        ): {"risk_score": 7, "risk_tier": "low", "created_at": base + timedelta(minutes=1)},
    }

    response = client.get("/sessions", params={"page": 1, "limit": 100})
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1 and body["limit"] == 100

    expected = _expected_sessions()
    assert body["total"] == len(expected)
    assert len(body["sessions"]) == min(100, len(expected))
    for item in body["sessions"]:
        assert set(item) == {
            "session_id",
            "user_id",
            "risk_score",
            "risk_tier",
            "device_fingerprint",
            "ip_address",
            "timestamp",
        }
    for item, expected_item in zip(body["sessions"], expected[:100]):
        _assert_item_matches(item, expected_item)

    # The freshly seeded sessions are present with their exact seeded values.
    _, all_items = _collect_all_sessions()
    by_id = {item["session_id"]: item for item in all_items}
    for session_external_id, seeded_values in seeded.items():
        item = by_id[session_external_id]
        assert item["user_id"] == user_id
        assert item["risk_score"] == seeded_values["risk_score"]
        assert item["risk_tier"] == seeded_values["risk_tier"]
        assert item["device_fingerprint"] == DOMAIN_DEVICE
        assert datetime.fromisoformat(item["timestamp"]) == seeded_values["created_at"]


def test_list_sessions_pagination_is_a_stable_partition(user_id: str) -> None:
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)
    seeded = [
        _seed_scored_session(
            user_id,
            created_at=base + timedelta(seconds=30 * index),
            risk_score=20 + index,
            risk_tier="low",
            ip_address=f"192.168.2.{10 + index}",
        )
        for index in range(5)
    ]

    collected: list[str] = []
    page = 1
    last_total = None
    while True:
        response = client.get("/sessions", params={"page": page, "limit": 2})
        assert response.status_code == 200
        body = response.json()
        assert body["page"] == page and body["limit"] == 2
        last_total = body["total"]
        items = body["sessions"]
        collected.extend(item["session_id"] for item in items)
        if len(items) < 2:
            break
        page += 1
        assert page < 1000, "pagination did not converge"

    expected = _expected_sessions()
    assert last_total == len(expected)
    assert collected == [item["session_id"] for item in expected]
    for session_external_id in seeded:
        assert collected.count(session_external_id) == 1


def test_list_sessions_page_beyond_end_is_empty(user_id: str) -> None:
    _seed_scored_session(
        user_id,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=15),
        risk_score=33,
        risk_tier="medium",
        ip_address="192.168.1.60",
    )
    expected = _expected_sessions()
    far_page = len(expected) // 3 + 50

    response = client.get("/sessions", params={"page": far_page, "limit": 3})
    assert response.status_code == 200
    body = response.json()
    assert body["sessions"] == []
    assert body["total"] == len(expected)
    assert body["page"] == far_page and body["limit"] == 3


def test_list_sessions_risk_tier_filter(user_id: str) -> None:
    now = datetime.now(timezone.utc)
    sid_low = _seed_scored_session(
        user_id,
        created_at=now - timedelta(minutes=10),
        risk_score=5,
        risk_tier="low",
        ip_address="192.168.1.20",
    )
    sid_medium = _seed_scored_session(
        user_id,
        created_at=now - timedelta(minutes=5),
        risk_score=55,
        risk_tier="medium",
        ip_address="192.168.1.21",
    )

    for tier in ("low", "medium", "high"):
        total, items = _collect_all_sessions(risk_tier=tier)
        expected = _expected_sessions(risk_tier=tier)
        assert total == len(expected)
        assert [item["session_id"] for item in items] == [
            expected_item["session_id"] for expected_item in expected
        ]
        for item, expected_item in zip(items, expected):
            assert item["risk_tier"] == tier
            _assert_item_matches(item, expected_item)

    _, low_items = _collect_all_sessions(risk_tier="low")
    _, medium_items = _collect_all_sessions(risk_tier="medium")
    low_ids = {item["session_id"] for item in low_items}
    medium_ids = {item["session_id"] for item in medium_items}
    assert sid_low in low_ids and sid_low not in medium_ids
    assert sid_medium in medium_ids and sid_medium not in low_ids


def test_list_sessions_invalid_params_return_422() -> None:
    for params in (
        {"risk_tier": "critical"},
        {"risk_tier": "MEDIUM"},
        {"risk_tier": ""},
        {"page": 0},
        {"limit": 0},
        {"limit": 101},
    ):
        response = client.get("/sessions", params=params)
        assert response.status_code == 422, f"params={params}: {response.text}"


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}
# ---------------------------------------------------------------------------


def test_session_detail_matches_persisted_after_score(user_id: str) -> None:
    score_response = client.post(
        "/session/score",
        json={
            "user_id": user_id,
            "ip_address": "192.168.1.99",
            "device_fingerprint": DOMAIN_DEVICE,
        },
    )
    assert score_response.status_code == 200
    scored = score_response.json()
    session_external_id = scored["session_id"]

    response = client.get(f"/sessions/{session_external_id}")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "session_id",
        "user_id",
        "risk_score",
        "risk_tier",
        "contributing_signals",
        "device_fingerprint",
        "ip_address",
        "timestamp",
        "history",
    }
    assert body["session_id"] == session_external_id
    assert body["user_id"] == user_id
    assert body["risk_score"] == scored["risk_score"]
    assert body["risk_tier"] == scored["risk_tier"]
    assert body["contributing_signals"] == scored["contributing_signals"]
    assert set(body["contributing_signals"]) == {
        "geo_velocity_kmh",
        "geo_location_status",
        "device_mismatch_score",
        "token_reuse_flag",
        "login_burst_count",
    }
    assert body["device_fingerprint"] == DOMAIN_DEVICE
    assert body["ip_address"] == "192.168.1.99"

    with SessionLocal() as db:
        session_row = db.execute(
            select(Session).where(Session.session_id == session_external_id)
        ).scalar_one()
        event = db.execute(
            select(RiskEvent).where(RiskEvent.session_id == session_row.id)
        ).scalar_one()
    assert datetime.fromisoformat(body["timestamp"]) == session_row.created_at
    assert len(body["history"]) == 1
    assert body["history"][0]["event"] == "risk_scored"
    assert datetime.fromisoformat(body["history"][0]["timestamp"]) == event.created_at


def test_session_detail_returns_latest_event_and_full_history(user_id: str) -> None:
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
    session_external_id = _seed_scored_session(
        user_id,
        created_at=base,
        risk_score=10,
        risk_tier="low",
        ip_address="192.168.1.30",
        signals=_signals(risk_score_unrounded=10.0),
    )
    # Add a second, newer event to the same session.
    with SessionLocal() as db:
        session_row = db.execute(
            select(Session).where(Session.session_id == session_external_id)
        ).scalar_one()
        db.add(
            RiskEvent(
                session_id=session_row.id,
                risk_score=88,
                risk_tier="high",
                contributing_signals=_signals(
                    geo_velocity_kmh=950.0,
                    geo_location_status="ok",
                    device_mismatch_score=1.0,
                    token_reuse_flag=True,
                    login_burst_count=30,
                    risk_score_unrounded=85.0,
                ),
                created_at=base + timedelta(minutes=5),
            )
        )
        db.commit()

    response = client.get(f"/sessions/{session_external_id}")
    assert response.status_code == 200
    body = response.json()
    # Score/tier/signals come from the MOST RECENT event (88 / high).
    assert body["risk_score"] == 88
    assert body["risk_tier"] == "high"
    assert body["contributing_signals"]["geo_velocity_kmh"] == 950.0
    assert body["contributing_signals"]["token_reuse_flag"] is True
    assert "risk_score_unrounded" not in body["contributing_signals"]
    # History lists every event, chronological ascending.
    history = body["history"]
    assert [entry["event"] for entry in history] == ["risk_scored", "risk_scored"]
    times = [datetime.fromisoformat(entry["timestamp"]) for entry in history]
    assert times == [base, base + timedelta(minutes=5)]


def test_session_detail_not_found_returns_404() -> None:
    for unknown in (str(uuid.uuid4()), "does-not-exist-123"):
        response = client.get(f"/sessions/{unknown}")
        assert response.status_code == 404
        assert response.json() == {"detail": "Session not found"}


def test_event_less_session_is_excluded_and_returns_404(user_id: str) -> None:
    """Event-less sessions are excluded by design (INNER JOIN): the pipeline
    persists session + risk event transactionally, and the read API covers
    only scored sessions."""
    now = datetime.now(timezone.utc)
    external_id = str(uuid.uuid4())
    with SessionLocal() as db:
        user = User(user_id=user_id)
        db.add(user)
        db.flush()
        db.add(
            Session(
                session_id=external_id,
                user_id=user.id,
                device_fingerprint=DOMAIN_DEVICE,
                ip_address="192.168.1.40",
                created_at=now,
                last_seen_at=now,
            )
        )
        db.commit()

    detail = client.get(f"/sessions/{external_id}")
    assert detail.status_code == 404
    assert detail.json() == {"detail": "Session not found"}

    _, items = _collect_all_sessions()
    assert external_id not in {item["session_id"] for item in items}
