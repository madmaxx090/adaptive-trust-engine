"""Live risk pipeline: history lookup, live signals, frozen scoring, persistence.

One pass per request, in this exact order:

    load history (Postgres) -> live signals (device, geo, token, burst)
    -> frozen baseline scorer (imported, unmodified)
    -> ML signal (Isolation Forest, loaded once at startup)
    -> persist to Postgres (commit first) -> update Redis state (after commit)
    -> response

Failure policy:
- Postgres unavailable (history read or persistence), including client-side
  parameter rejection by the database driver (e.g. NUL bytes in text
  parameters), Redis unavailable during signal computation, the GeoLite2
  database missing, or the ML signal unavailable (missing/corrupt artifact,
  invalid features): the corresponding exception propagates (mapped to HTTP
  503 with a descriptive message) and nothing partial is persisted.
- Redis writes AFTER a successful Postgres commit are fail-soft: they are
  logged, not raised. The risk decision and its audit trail are already
  durable in Postgres (the source of truth); the next request self-heals from
  Postgres, and failing a committed request would invite duplicate retries.

The login-burst sorted set is written during signal computation by nature: a
rolling window cannot count the current attempt without recording it. The
burst therefore counts scoring attempts, even if the later database write
fails (attempts are what matter for bursts).
"""

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import RiskEvent, Session, User
from app.services import geo
from app.services.baseline_scorer import score_session as frozen_score_session
from app.services.ml_runtime import ml_runtime
from app.services.session_store import (
    store_refresh_token_hash,
    update_session_context,
    verify_refresh_token_hash,
)

logger = logging.getLogger(__name__)

LOGIN_BURST_WINDOW_SECONDS: int = 60
LOGIN_BURST_KEY_TTL_SECONDS: int = 120

_redis: redis.Redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)


class RiskPipelineUnavailableError(Exception):
    """Infrastructure failure during live scoring (mapped to HTTP 503)."""


@dataclass(frozen=True)
class ScoreOutcome:
    """Result of one live scoring pass (the unrounded score stays internal)."""

    risk_score: int
    risk_tier: str
    contributing_signals: dict[str, Any]
    session_id: str
    ml_anomaly_flag: bool
    ml_decision_score: float


def process_session_score(
    user_id: str,
    ip_address: str,
    device_fingerprint: str,
    refresh_token: str | None = None,
) -> ScoreOutcome:
    """Run one live scoring pass for a session request."""
    previous = _load_previous_session(user_id)

    # Single server-generated UTC timestamp for persistence and elapsed time.
    now = datetime.now(timezone.utc)

    device_mismatch = _compute_device_mismatch(previous, device_fingerprint)
    velocity_kmh, geo_status, current_coordinates = geo.compute_geo_velocity(
        previous_ip=previous.ip_address if previous is not None else None,
        previous_last_seen_at=previous.last_seen_at if previous is not None else None,
        current_ip=ip_address,
        current_time=now,
    )
    token_hash = (
        _hash_refresh_token(refresh_token) if refresh_token is not None else None
    )
    token_reuse_flag = _compute_token_reuse(
        previous.session_id if previous is not None else None, token_hash
    )
    burst_count = _record_login_attempt_burst(user_id)

    # Frozen baseline scorer (imported as-is; the prediction output exists for
    # the offline evaluations and is not used by the live endpoint).
    raw_score, risk_tier, _prediction = frozen_score_session(
        velocity_kmh, device_mismatch, token_reuse_flag, burst_count
    )

    # Additional ML signal: scored on the same four signal values with the
    # frozen Phase 5 feature encoding (app/services/ml_runtime.py). The
    # rule-based baseline above is untouched and both signals are returned
    # distinctly -- nothing is fused into a new score. An ML failure raises
    # MLSignalError (sanitized 503 at the API; no silent fallback).
    ml_anomaly_flag, ml_decision_score = ml_runtime.score(
        velocity_kmh, device_mismatch, token_reuse_flag, burst_count
    )

    contributing_signals: dict[str, Any] = {
        "geo_velocity_kmh": velocity_kmh,
        "geo_location_status": geo_status,
        "device_mismatch_score": device_mismatch,
        "token_reuse_flag": token_reuse_flag,
        "login_burst_count": burst_count,
    }

    # risk_score is an int per the API/DB contract: rounded from the unrounded
    # frozen score ONLY for display/persistence -- the tier above was
    # classified by the frozen scorer on the unrounded value.
    risk_score = int(round(raw_score))
    persisted_signals = {**contributing_signals, "risk_score_unrounded": raw_score}

    # Postgres first (durable audit trail), Redis second (fast-access cache).
    session_id = _persist_scored_session(
        user_id=user_id,
        ip_address=ip_address,
        device_fingerprint=device_fingerprint,
        now=now,
        risk_score=risk_score,
        risk_tier=risk_tier,
        contributing_signals=persisted_signals,
    )

    location = (
        f"{current_coordinates[0]:.4f},{current_coordinates[1]:.4f}"
        if current_coordinates is not None
        else "unknown"
    )
    _update_redis_after_commit(
        session_id=session_id,
        device_fingerprint=device_fingerprint,
        location=location,
        last_seen_at=now,
        token_hash=token_hash,
    )

    return ScoreOutcome(
        risk_score=risk_score,
        risk_tier=risk_tier,
        contributing_signals=contributing_signals,
        session_id=session_id,
        ml_anomaly_flag=ml_anomaly_flag,
        ml_decision_score=ml_decision_score,
    )


def _load_previous_session(user_id: str) -> Session | None:
    """Most recent session for the user, or None (deterministic ordering)."""
    try:
        with SessionLocal() as db:
            user = db.execute(
                select(User).where(User.user_id == user_id)
            ).scalar_one_or_none()
            if user is None:
                return None
            return db.execute(
                select(Session)
                .where(Session.user_id == user.id)
                .order_by(
                    Session.last_seen_at.desc(),
                    Session.created_at.desc(),
                    Session.id.desc(),
                )
                .limit(1)
            ).scalar_one_or_none()
    except (SQLAlchemyError, ValueError) as exc:
        # psycopg2 rejects NUL bytes in text parameters client-side (before
        # any SQL is sent) with a plain ValueError -- not a DBAPI/SQLAlchemy
        # error class, so SQLAlchemy re-raises it unwrapped. Normalized here
        # to the established infrastructure-failure path (logged server-side,
        # sanitized 503 to the client); the input itself is never altered.
        raise RiskPipelineUnavailableError(
            f"PostgreSQL unavailable during session history lookup: {exc}"
        ) from exc


def _compute_device_mismatch(previous: Session | None, device_fingerprint: str) -> float:
    """Binary device-mismatch signal.

    The validated offline baseline supports a continuous device-mismatch
    signal; this live implementation derives it as a binary value based on
    fingerprint equality -- a documented simplification for the initial live
    pipeline.
    """
    if previous is None:
        return 0.0
    return 0.0 if previous.device_fingerprint == device_fingerprint else 1.0


def _hash_refresh_token(refresh_token: str) -> str:
    """Server-side SHA-256 of the raw refresh token (only hashes are stored)."""
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def _compute_token_reuse(
    previous_session_id: str | None, token_hash: str | None
) -> bool:
    """Token reuse = the same token the previous session already holds is
    being presented again.

    Reuses the existing ``verify_refresh_token_hash`` exactly as-is (a missing
    stored key counts as no match). No previous session or no token supplied
    -> False.
    """
    if previous_session_id is None or token_hash is None:
        return False
    try:
        return verify_refresh_token_hash(previous_session_id, token_hash)
    except redis.RedisError as exc:
        raise RiskPipelineUnavailableError(
            f"Redis unavailable during token-reuse verification: {exc}"
        ) from exc


def _record_login_attempt_burst(user_id: str) -> int:
    """Genuine rolling 60-second login-burst count via a Redis sorted set.

    Each attempt is stored as a member scored by its unix timestamp; entries
    with score <= now - window are pruned, and the remaining entries (the
    current attempt included) are counted. One transactional pipeline per
    call. Key pattern: ``ate:login_burst:{user_id}``.
    """
    key = f"ate:login_burst:{user_id}"
    now_ts = time.time()
    member = f"{time.time_ns()}:{uuid.uuid4().hex}"
    try:
        with _redis.pipeline(transaction=True) as pipe:
            pipe.zadd(key, {member: now_ts})
            pipe.zremrangebyscore(key, "-inf", now_ts - LOGIN_BURST_WINDOW_SECONDS)
            pipe.expire(key, LOGIN_BURST_KEY_TTL_SECONDS)
            pipe.zcard(key)
            results = pipe.execute()
    except redis.RedisError as exc:
        raise RiskPipelineUnavailableError(
            f"Redis unavailable during login-burst computation: {exc}"
        ) from exc
    return int(results[3])


def _persist_scored_session(
    user_id: str,
    ip_address: str,
    device_fingerprint: str,
    now: datetime,
    risk_score: int,
    risk_tier: str,
    contributing_signals: dict[str, Any],
) -> str:
    """Persist user (find-or-create), session, and risk event; returns session_id.

    The user row is created with INSERT ... ON CONFLICT DO NOTHING followed by
    a re-select, which makes concurrent first-ever requests race-safe: a losing
    INSERT waits for the winner's commit, becomes a no-op, and the re-select
    then finds the committed row -- no IntegrityError and no retry loop. The
    conflict target is scoped to the users.user_id unique index only, so any
    other database error still propagates (nothing is swallowed silently).

    Runs in a single Postgres transaction: any failure rolls back and raises,
    and the caller never reaches the Redis update step (nothing partial).
    """
    try:
        with SessionLocal() as db:
            db.execute(
                pg_insert(User)
                .values(user_id=user_id)
                .on_conflict_do_nothing(index_elements=["user_id"])
            )
            user_pk = db.execute(
                select(User.id).where(User.user_id == user_id)
            ).scalar_one()

            session_row = Session(
                session_id=str(uuid.uuid4()),
                user_id=user_pk,
                device_fingerprint=device_fingerprint,
                ip_address=ip_address,
                created_at=now,
                last_seen_at=now,
            )
            db.add(session_row)
            db.flush()

            db.add(
                RiskEvent(
                    session_id=session_row.id,
                    risk_score=risk_score,
                    risk_tier=risk_tier,
                    contributing_signals=contributing_signals,
                )
            )
            db.commit()
            return session_row.session_id
    except (SQLAlchemyError, ValueError) as exc:
        # Same normalization as in _load_previous_session: psycopg2's
        # client-side parameter rejection (e.g. NUL bytes in text parameters)
        # raises a plain ValueError that SQLAlchemy re-raises unwrapped, and
        # it must reach the sanitized-503 path instead of escaping as an
        # unhandled 500. The exception still occurs before any commit, so
        # nothing partial is persisted.
        raise RiskPipelineUnavailableError(
            f"Failed to persist session/risk event to PostgreSQL: {exc}"
        ) from exc


def _update_redis_after_commit(
    session_id: str,
    device_fingerprint: str,
    location: str,
    last_seen_at: datetime,
    token_hash: str | None,
) -> None:
    """Fail-soft Redis cache updates after a durable Postgres commit.

    Failures are logged, never raised (see module docstring). Each write is
    guarded individually so one failure does not skip the other.
    """
    if token_hash is not None:
        try:
            store_refresh_token_hash(session_id, token_hash)
        except redis.RedisError:
            logger.exception(
                "Post-commit Redis write failed: refresh token hash (session %s)",
                session_id,
            )
    try:
        update_session_context(session_id, device_fingerprint, location, last_seen_at)
    except redis.RedisError:
        logger.exception(
            "Post-commit Redis write failed: session context (session %s)",
            session_id,
        )
