"""Session scoring route (stub until the ATE risk engine is implemented)."""

from fastapi import APIRouter

from app.schemas.session import SessionScoreRequest, SessionScoreResponse

router = APIRouter(tags=["session"])


@router.post("/session/score", response_model=SessionScoreResponse)
async def score_session(payload: SessionScoreRequest) -> SessionScoreResponse:
    # Hardcoded stub — no risk-scoring logic yet.
    return SessionScoreResponse(risk_score=0, risk_tier="low")
