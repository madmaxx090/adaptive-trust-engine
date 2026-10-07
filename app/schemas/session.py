"""Session score and session-read request/response schemas."""

from datetime import datetime

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
    """Explainability payload for one risk decision.

    The first five fields are the live signals that fed the frozen baseline
    scorer. Everything after them is additive and reported alongside that
    scorer: the ``user_baseline_status`` / ``device_seen_before_count`` /
    ``geo_velocity_user_percentile`` per-user baseline, and the
    ``device_cohort_user_count`` / ``token_cohort_user_count`` cross-account
    cohort signal. None of them feed the frozen scorer and none affect
    ``risk_score`` or ``risk_tier``. They all default to None so risk events
    persisted before these signals were added still validate.
    """

    geo_velocity_kmh: float
    geo_location_status: str
    device_mismatch_score: float
    token_reuse_flag: bool
    login_burst_count: int
    user_baseline_status: str | None = Field(
        default=None,
        description=(
            "\"ok\" when the user has prior sessions, \"no_history\" on cold "
            "start; null for risk events persisted before the per-user "
            "baseline was added."
        ),
    )
    device_seen_before_count: int | None = Field(
        default=None,
        description=(
            "How many of this user's prior sessions used this exact device "
            "fingerprint (0 = a new device for this user)."
        ),
    )
    geo_velocity_user_percentile: float | None = Field(
        default=None,
        description=(
            "Percentile rank (0-100) of the current geo velocity within this "
            "user's measured velocity history; null when there is no measured "
            "history to rank against."
        ),
    )
    device_cohort_user_count: int | None = Field(
        default=None,
        description=(
            "Distinct accounts that have used this exact device fingerprint in "
            "the last 24 hours, this request included: 1 means this account "
            "alone, more means the device is shared across accounts "
            "(credential-stuffing indicator)."
        ),
    )
    token_cohort_user_count: int | None = Field(
        default=None,
        description=(
            "Distinct accounts associated with this exact refresh-token hash in "
            "the last 24 hours, this request included: normally 1, so anything "
            "higher means one token is being presented across accounts. Null "
            "when the request carried no refresh token (nothing observable)."
        ),
    )


class SessionScoreResponse(BaseModel):
    risk_score: int
    risk_tier: str
    contributing_signals: ContributingSignals
    session_id: str
    # Additive ML signal (Isolation Forest alongside, not fused with, the
    # rule-based baseline): binary anomaly flag + raw decision score.
    ml_anomaly_flag: bool = Field(
        description="Isolation Forest anomaly flag (True = flagged/suspicious)."
    )
    ml_decision_score: float = Field(
        description=(
            "Raw uncalibrated Isolation Forest decision_function value "
            "(higher = more normal; negative = anomalous; not a probability)."
        )
    )


class SessionListItem(BaseModel):
    """One row of the read-only session list (latest risk event's score/tier)."""

    session_id: str
    user_id: str
    risk_score: int
    risk_tier: str
    device_fingerprint: str
    ip_address: str
    timestamp: datetime


class SessionListResponse(BaseModel):
    total: int
    page: int
    limit: int
    sessions: list[SessionListItem]


class SessionHistoryEntry(BaseModel):
    """A per-risk-event history entry for a session (chronological)."""

    event: str
    timestamp: datetime


class SessionDetailResponse(BaseModel):
    session_id: str
    user_id: str
    risk_score: int
    risk_tier: str
    contributing_signals: ContributingSignals
    device_fingerprint: str
    ip_address: str
    timestamp: datetime
    history: list[SessionHistoryEntry]
    # Additive ML signal, persisted with the risk event so this endpoint
    # reports the same values POST /session/score returned. None means the
    # risk event predates ML persistence (unknown), not "no anomaly".
    ml_anomaly_flag: bool | None = Field(
        default=None,
        description=(
            "Isolation Forest anomaly flag as returned by POST /session/score; "
            "null for risk events persisted before the ML signal was stored."
        ),
    )
    ml_decision_score: float | None = Field(
        default=None,
        description=(
            "Raw uncalibrated Isolation Forest decision_function value; "
            "null for risk events persisted before the ML signal was stored."
        ),
    )
