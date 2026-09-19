"""Session score request/response schemas."""

from pydantic import BaseModel


class SessionScoreRequest(BaseModel):
    user_id: str
    ip_address: str
    device_fingerprint: str


class SessionScoreResponse(BaseModel):
    risk_score: int
    risk_tier: str
