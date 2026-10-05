"""Audit log route: read-only view of the append-only audit trail.

The write side lives in the scoring pipeline
(``app.services.risk_pipeline._persist_scored_session``), which appends one
``risk_scored`` entry in the same transaction as the session and its risk
event, so a scored session can never exist without its audit entry.
"""

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models import AuditLog, Session, User
from app.schemas.audit import AuditLogEntry, AuditLogResponse

router = APIRouter(tags=["audit"])


@router.get("/audit-log", response_model=AuditLogResponse)
def list_audit_log(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    event_type: str | None = Query(None, max_length=255),
) -> AuditLogResponse:
    """Newest-first audit trail, optionally filtered by event type.

    Uses LEFT JOINs rather than the INNER JOINs the session reads use:
    ``audit_log.session_id`` is nullable by design, and an audit entry must
    never disappear from the trail just because it is not tied to a session
    row. ``user_id`` is resolved through the session for the same reason it is
    also denormalized inside ``details``.
    """
    base = (
        select(
            AuditLog.id,
            AuditLog.event_type,
            AuditLog.details,
            AuditLog.created_at,
            Session.session_id,
            User.user_id,
        )
        .outerjoin(Session, AuditLog.session_id == Session.id)
        .outerjoin(User, Session.user_id == User.id)
    )
    if event_type is not None:
        base = base.where(AuditLog.event_type == event_type)

    with SessionLocal() as db:
        total = db.execute(
            select(func.count()).select_from(base.subquery())
        ).scalar_one()
        rows = db.execute(
            base.order_by(AuditLog.created_at.desc(), AuditLog.id)
            .offset((page - 1) * limit)
            .limit(limit)
        ).all()

    return AuditLogResponse(
        total=total,
        page=page,
        limit=limit,
        entries=[
            AuditLogEntry(
                id=row.id,
                event_type=row.event_type,
                session_id=row.session_id,
                user_id=row.user_id,
                risk_score=(row.details or {}).get("risk_score"),
                risk_tier=(row.details or {}).get("risk_tier"),
                details=row.details,
                timestamp=row.created_at,
            )
            for row in rows
        ],
    )
