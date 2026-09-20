"""Session score request/response schemas."""

from pydantic import BaseModel, Field


class SessionScoreRequest(BaseModel):
    # Length constraints only: empty and oversized values are rejected; a
    # malformed-but-non-empty IP is NOT a validation error (it still flows to
    # the geo layer and is scored as "invalid_ip").
    user_id: str = Field(min_length=1, max_length=255)
    ip_address: str = Field(min_length=1, max_length=45)
    device_fingerprint: str = Field(min_length=1, max_length=255)
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
