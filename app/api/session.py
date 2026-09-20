"""Session scoring route: live risk signals via the frozen baseline scorer."""

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
        # Infrastructure unavailable -> 503 with a descriptive reason.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return SessionScoreResponse(
        risk_score=outcome.risk_score,
        risk_tier=outcome.risk_tier,
        contributing_signals=ContributingSignals(**outcome.contributing_signals),
        session_id=outcome.session_id,
    )
