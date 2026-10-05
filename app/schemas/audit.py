"""Audit log read schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditLogEntry(BaseModel):
    """One append-only audit trail entry.

    ``risk_score`` / ``risk_tier`` are lifted out of ``details`` for
    convenience; they are null for event types that do not represent a risk
    decision, so the shape stays valid as new event types are added.
    """

    id: uuid.UUID
    event_type: str
    # External session id (sessions.session_id). Null when the entry is not
    # tied to a session, which the audit_log schema explicitly allows.
    session_id: str | None = None
    user_id: str | None = None
    risk_score: int | None = None
    risk_tier: str | None = None
    details: dict[str, Any] | None = None
    timestamp: datetime


class AuditLogResponse(BaseModel):
    total: int
    page: int
    limit: int
    entries: list[AuditLogEntry]
