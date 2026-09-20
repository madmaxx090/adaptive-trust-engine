"""Session scoring route: live risk signals via the frozen baseline scorer."""

import logging

from fastapi import APIRouter, HTTPException

from app.schemas.session import (
    ContributingSignals,
    SessionScoreRequest,
    SessionScoreResponse,
)
from app.services.geo import GeoIPDatabaseUnavailableError
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
    except (GeoIPDatabaseUnavailableError, RiskPipelineUnavailableError) as exc:
        # Infrastructure unavailable -> 503. The full underlying exception
        # (including driver SQL/constraint details) is logged server-side only.
        logger.exception("Scoring temporarily unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=SERVICE_UNAVAILABLE_DETAIL) from exc

    return SessionScoreResponse(
        risk_score=outcome.risk_score,
        risk_tier=outcome.risk_tier,
        contributing_signals=ContributingSignals(**outcome.contributing_signals),
        session_id=outcome.session_id,
    )
