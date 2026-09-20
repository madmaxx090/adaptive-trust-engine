"""Fixed rule-based baseline scorer for ATE session risk.

This module implements the deliberately static rule-based baseline that serves
as the reference point for the later ML/adaptive model and the comparative
evaluation stage of the ATE research:

    fixed rule-based baseline -> ML / adaptive model -> comparative evaluation

Research-methodology notes:
- The four signal weights below are heuristic design choices. They are NOT
  learned, fitted, optimized, or derived statistically from the synthetic
  dataset, and must not be tuned against evaluation metrics.
- The normalization caps (1000 km/h, burst count 30) are fixed constants.
- The baseline is not machine learning and not adaptive: identical inputs
  always produce identical scores.
"""

GEO_VELOCITY_CAP_KMH: float = 1000.0
LOGIN_BURST_CAP: int = 30

# Heuristic baseline weights (sum = 1.00). Fixed by design; not learned.
WEIGHTS: dict[str, float] = {
    "geo_velocity": 0.30,
    "device_mismatch": 0.30,
    "token_reuse": 0.25,
    "login_burst": 0.15,
}

# Tier boundaries from the project proposal: Low 0-40, Medium 41-70,
# High 71-100. Applied to unrounded scores:
#   score <= 40.0 -> low;  40.0 < score <= 70.0 -> medium;  score > 70.0 -> high
TIER_LOW_MAX: float = 40.0
TIER_MEDIUM_MAX: float = 70.0

# Evaluation mapping: Medium and High are treated as flagged/suspicious
# sessions (prediction = 1); Low is treated as non-flagged (prediction = 0).
PREDICTION_BY_TIER: dict[str, int] = {"low": 0, "medium": 1, "high": 1}


def geo_velocity_score(kmh: float) -> float:
    """Normalize geo-velocity: min(geo_velocity_kmh / 1000, 1) * 100."""
    return min(kmh / GEO_VELOCITY_CAP_KMH, 1.0) * 100.0


def device_score(mismatch: float) -> float:
    """Normalize device mismatch: device_mismatch_score * 100."""
    return mismatch * 100.0


def token_score(flag: bool) -> float:
    """Normalize token reuse: 100 if token_reuse_flag else 0."""
    return 100.0 if flag else 0.0


def burst_score(count: int) -> float:
    """Normalize login burst: min(login_burst_count / 30, 1) * 100."""
    return min(count / LOGIN_BURST_CAP, 1.0) * 100.0


def compute_risk_score(
    geo_kmh: float,
    device_mismatch: float,
    token_reuse_flag: bool,
    burst_count: int,
) -> float:
    """Weighted fusion of the normalized signals (0-100, not rounded)."""
    return (
        geo_velocity_score(geo_kmh) * WEIGHTS["geo_velocity"]
        + device_score(device_mismatch) * WEIGHTS["device_mismatch"]
        + token_score(token_reuse_flag) * WEIGHTS["token_reuse"]
        + burst_score(burst_count) * WEIGHTS["login_burst"]
    )


def classify_tier(score: float) -> str:
    """Classify an unrounded risk score into 'low', 'medium', or 'high'."""
    if score <= TIER_LOW_MAX:
        return "low"
    if score <= TIER_MEDIUM_MAX:
        return "medium"
    return "high"


def predict_from_tier(tier: str) -> int:
    """Map a tier to the evaluation prediction (medium/high -> 1)."""
    return PREDICTION_BY_TIER[tier]


def score_session(
    geo_kmh: float,
    device_mismatch: float,
    token_reuse_flag: bool,
    burst_count: int,
) -> tuple[float, str, int]:
    """Score one session: (risk_score, risk_tier, prediction)."""
    score = compute_risk_score(geo_kmh, device_mismatch, token_reuse_flag, burst_count)
    tier = classify_tier(score)
    return score, tier, predict_from_tier(tier)
