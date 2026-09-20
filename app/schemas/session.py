"""Session score request/response schemas."""

from pydantic import BaseModel


class SessionScoreRequest(BaseModel):
    user_id: str
    ip_address: str
    device_fingerprint: str
    # Optional live refresh token: enables token-reuse detection against the
    # user's previous session (SHA-256 server-side; only hashes are stored).
    refresh_token: str | None = None


class ContributingSignals(BaseModel):
    """The live signals that fed the frozen baseline scorer (explainability)."""

    geo_velocity_kmh: float
    geo_location_status: str
    device_mismatch_score: float
    token_reuse_flag: bool
    login_burst_count: int


class SessionScoreResponse(BaseModel):
    risk_score: int
    risk_tier: str
    contributing_signals: ContributingSignals
    session_id: str
