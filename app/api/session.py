"""Session scoring route: live baseline + ML risk signals."""

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.sql.selectable import Subquery

from app.core.database import SessionLocal
from app.models import RiskEvent, Session, User
from app.schemas.session import (
    ContributingSignals,
    SessionDetailResponse,
    SessionHistoryEntry,
    SessionListItem,
    SessionListResponse,
    SessionScoreRequest,
    SessionScoreResponse,
)
from app.services.geo import GeoIPDatabaseUnavailableError
from app.services.ml_runtime import MLSignalError
from app.services.risk_pipeline import (
    RiskPipelineUnavailableError,
    process_session_score,
)

router = APIRouter(tags=["session"])

logger = logging.getLogger(__name__)

# Fixed, generic client-facing body for infrastructure failures: never expose
# SQL statements, table/column/constraint names, exception details, or other
# database internals (those are logged server-side only).
SERVICE_UNAVAILABLE_DETAIL = (
    "Scoring service temporarily unavailable. Please try again later."
)


@router.post("/session/score", response_model=SessionScoreResponse)
def score_session(payload: SessionScoreRequest) -> SessionScoreResponse:
    # Synchronous handler: the live pipeline uses sync Postgres/Redis clients.
    try:
        outcome = process_session_score(
            user_id=payload.user_id,
            ip_address=payload.ip_address,
            device_fingerprint=payload.device_fingerprint,
            refresh_token=payload.refresh_token,
        )
    except (
        GeoIPDatabaseUnavailableError,
        RiskPipelineUnavailableError,
        MLSignalError,
    ) as exc:
        # Infrastructure unavailable -> 503. The full underlying exception
        # (including driver SQL/constraint details) is logged server-side only.
        logger.exception("Scoring temporarily unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=SERVICE_UNAVAILABLE_DETAIL) from exc

    return SessionScoreResponse(
        risk_score=outcome.risk_score,
        risk_tier=outcome.risk_tier,
        contributing_signals=ContributingSignals(**outcome.contributing_signals),
        session_id=outcome.session_id,
        ml_anomaly_flag=outcome.ml_anomaly_flag,
        ml_decision_score=outcome.ml_decision_score,
    )


# ---------------------------------------------------------------------------
# Read-only session retrieval (queries data persisted by the scoring pipeline)
# ---------------------------------------------------------------------------


def _latest_risk_event_subquery() -> Subquery:
    """Latest risk event per session: created_at desc, id as tie-break.

    DISTINCT ON is a no-op for today's data (the pipeline persists exactly one
    event per session) but stays correct if a session accumulates more.
    """
    return (
        select(
            RiskEvent.session_id.label("session_pk"),
            RiskEvent.risk_score,
            RiskEvent.risk_tier,
            RiskEvent.contributing_signals,
            RiskEvent.created_at,
        )
        .distinct(RiskEvent.session_id)
        .order_by(
            RiskEvent.session_id,
            RiskEvent.created_at.desc(),
            RiskEvent.id,
        )
        .subquery()
    )


@router.get("/sessions", response_model=SessionListResponse)
def list_sessions(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    risk_tier: Literal["low", "medium", "high"] | None = Query(None),
) -> SessionListResponse:
    """Read-only session list: newest first, tier filter on the latest event.

    Only scored sessions are returned: sessions without a risk event are
    excluded by design (INNER JOIN), because the production pipeline persists
    the session and its risk event in one transaction and the read contract
    exposes a non-null score/tier per session.
    """
    latest_event = _latest_risk_event_subquery()
    base = (
        select(
            Session.session_id,
            User.user_id,
            latest_event.c.risk_score,
            latest_event.c.risk_tier,
            Session.device_fingerprint,
            Session.ip_address,
            Session.created_at,
        )
        .join(User, Session.user_id == User.id)
        .join(latest_event, latest_event.c.session_pk == Session.id)
    )
    if risk_tier is not None:
        base = base.where(latest_event.c.risk_tier == risk_tier)

    with SessionLocal() as db:
        total = db.execute(
            select(func.count()).select_from(base.subquery())
        ).scalar_one()
        rows = db.execute(
            base.order_by(Session.created_at.desc(), Session.id)
            .offset((page - 1) * limit)
            .limit(limit)
        ).all()

    return SessionListResponse(
        total=total,
        page=page,
        limit=limit,
        sessions=[
            SessionListItem(
                session_id=row.session_id,
                user_id=row.user_id,
                risk_score=row.risk_score,
                risk_tier=row.risk_tier,
                device_fingerprint=row.device_fingerprint,
                ip_address=row.ip_address,
                timestamp=row.created_at,
            )
            for row in rows
        ],
    )


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
def get_session(session_id: str) -> SessionDetailResponse:
    """Read-only session detail: latest event's score/tier/signals + history.

    Event-less sessions are excluded by design (INNER JOIN): the production
    pipeline persists the session and its risk event transactionally, and the
    read contract covers only scored sessions.
    """
    latest_event = _latest_risk_event_subquery()
    with SessionLocal() as db:
        row = db.execute(
            select(
                Session.id,
                Session.session_id,
                User.user_id,
                latest_event.c.risk_score,
                latest_event.c.risk_tier,
                latest_event.c.contributing_signals,
                Session.device_fingerprint,
                Session.ip_address,
                Session.created_at,
            )
            .join(User, Session.user_id == User.id)
            .join(latest_event, latest_event.c.session_pk == Session.id)
            .where(Session.session_id == session_id)
        ).one_or_none()
        history_rows: list[datetime] = []
        if row is not None:
            history_rows = list(
                db.execute(
                    select(RiskEvent.created_at)
                    .where(RiskEvent.session_id == row.id)
                    .order_by(RiskEvent.created_at.asc(), RiskEvent.id)
                ).scalars()
            )

    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionDetailResponse(
        session_id=row.session_id,
        user_id=row.user_id,
        risk_score=row.risk_score,
        risk_tier=row.risk_tier,
        contributing_signals=ContributingSignals.model_validate(
            row.contributing_signals
        ),
        device_fingerprint=row.device_fingerprint,
        ip_address=row.ip_address,
        timestamp=row.created_at,
        history=[
            SessionHistoryEntry(event="risk_scored", timestamp=created_at)
            for created_at in history_rows
        ],
    )
