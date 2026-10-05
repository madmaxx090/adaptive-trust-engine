"""Audit trail tests: the write path in the scoring pipeline + GET /audit-log.

Same hygiene as the companion modules: unique per-test user ids and full
cleanup of Postgres rows and Redis keys via ``cleanup_user``.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.core.database import SessionLocal
from app.main import app
from app.models import AuditLog, Session, User
from tests.test_live_signals import cleanup_user

client = TestClient(app)

DOMAIN_DEVICE = "device-audit"


@pytest.fixture
def user_id() -> str:
    value = f"test-audit-{uuid.uuid4().hex[:12]}"
    yield value
    cleanup_user(value)  # teardown runs even when assertions fail


def _score(user_id: str, ip_address: str = "192.168.1.10") -> dict:
    response = client.post(
        "/session/score",
        json={
            "user_id": user_id,
            "ip_address": ip_address,
            "device_fingerprint": DOMAIN_DEVICE,
        },
    )
    assert response.status_code == 200
    return response.json()


def _audit_count_for_user(user_id: str) -> int:
    with SessionLocal() as db:
        return db.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.details["user_id"].as_string() == user_id)
        ).scalar_one()


def test_scored_session_writes_one_audit_entry(user_id: str) -> None:
    """Every scored session appends exactly one audit_log row, atomically."""
    scored = _score(user_id)

    with SessionLocal() as db:
        session_row = db.execute(
            select(Session).where(Session.session_id == scored["session_id"])
        ).scalar_one()
        entries = (
            db.execute(
                select(AuditLog).where(AuditLog.session_id == session_row.id)
            )
            .scalars()
            .all()
        )

    assert len(entries) == 1
    entry = entries[0]
    assert entry.event_type == "risk_scored"
    assert entry.details["user_id"] == user_id
    assert entry.details["risk_score"] == scored["risk_score"]
    assert entry.details["risk_tier"] == scored["risk_tier"]
    assert entry.details["scored_at"]
    # The summary mirrors the live signals the frozen scorer was given.
    for key, value in scored["contributing_signals"].items():
        assert entry.details["signals"][key] == value
    # Same single server-generated timestamp as the session row it audits.
    assert entry.created_at == session_row.created_at


def test_audit_entry_written_for_every_session_of_a_user(user_id: str) -> None:
    """Two scored sessions -> two audit entries (append-only, one per score)."""
    first = _score(user_id)
    second = _score(user_id, ip_address="192.168.1.11")
    assert first["session_id"] != second["session_id"]
    assert _audit_count_for_user(user_id) == 2


def test_audit_log_endpoint_exposes_real_entries(user_id: str) -> None:
    """GET /audit-log returns the persisted trail, not synthesized data."""
    scored = _score(user_id)

    response = client.get("/audit-log", params={"limit": 200})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"total", "page", "limit", "entries"}
    assert body["page"] == 1
    assert body["limit"] == 200
    assert body["total"] >= 1

    matching = [e for e in body["entries"] if e["session_id"] == scored["session_id"]]
    assert len(matching) == 1
    entry = matching[0]
    assert set(entry) == {
        "id",
        "event_type",
        "session_id",
        "user_id",
        "risk_score",
        "risk_tier",
        "details",
        "timestamp",
    }
    # session_id/user_id are resolved to their external string forms.
    assert entry["user_id"] == user_id
    assert entry["event_type"] == "risk_scored"
    assert entry["risk_tier"] == scored["risk_tier"]
    assert entry["risk_score"] == scored["risk_score"]
    assert entry["details"]["signals"] is not None


def test_audit_log_is_newest_first(user_id: str) -> None:
    _score(user_id)
    response = client.get("/audit-log", params={"limit": 50})
    timestamps = [e["timestamp"] for e in response.json()["entries"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_audit_log_event_type_filter(user_id: str) -> None:
    _score(user_id)

    filtered = client.get("/audit-log", params={"event_type": "risk_scored"})
    assert filtered.status_code == 200
    body = filtered.json()
    assert body["total"] >= 1
    assert all(e["event_type"] == "risk_scored" for e in body["entries"])

    empty = client.get("/audit-log", params={"event_type": "no_such_event_type"})
    assert empty.status_code == 200
    assert empty.json() == {"total": 0, "page": 1, "limit": 50, "entries": []}


def test_audit_log_pagination_bounds() -> None:
    for params in ({"page": 0}, {"limit": 0}, {"limit": 201}, {"page": "x"}):
        assert client.get("/audit-log", params=params).status_code == 422, params

    paged = client.get("/audit-log", params={"page": 2, "limit": 1})
    assert paged.status_code == 200
    body = paged.json()
    assert body["page"] == 2 and body["limit"] == 1
    assert len(body["entries"]) <= 1


def test_no_audit_entry_when_persistence_fails(user_id: str) -> None:
    """NUL byte -> sanitized 503 and nothing partial, audit trail included."""
    response = client.post(
        "/session/score",
        json={
            "user_id": user_id,
            "ip_address": "192.168.1.10",
            "device_fingerprint": f"{DOMAIN_DEVICE}\x00nul",
        },
    )
    assert response.status_code == 503
    with SessionLocal() as db:
        assert (
            db.execute(select(User).where(User.user_id == user_id)).scalar_one_or_none()
            is None
        )
    assert _audit_count_for_user(user_id) == 0


def test_audit_entry_without_session_is_still_listed() -> None:
    """audit_log.session_id is nullable: LEFT JOIN must not drop such entries."""
    entry_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(
            AuditLog(
                id=entry_id,
                event_type="test_unlinked_marker",
                session_id=None,
                details={"note": "no session"},
            )
        )
        db.commit()
    try:
        response = client.get(
            "/audit-log", params={"event_type": "test_unlinked_marker"}
        )
        assert response.status_code == 200
        entries = response.json()["entries"]
        assert [e["id"] for e in entries] == [str(entry_id)]
        assert entries[0]["session_id"] is None
        assert entries[0]["user_id"] is None
        # No risk decision recorded on this entry -> nulls, not fabricated 0s.
        assert entries[0]["risk_score"] is None
        assert entries[0]["risk_tier"] is None
    finally:
        with SessionLocal() as db:
            db.execute(delete(AuditLog).where(AuditLog.id == entry_id))
            db.commit()
