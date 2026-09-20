"""Isolation Forest anomaly detection model for ATE session risk.

Second stage of the ATE research progression:

    fixed rule-based baseline -> ML / adaptive model -> comparative evaluation

This module wraps scikit-learn's IsolationForest with frozen experimental
parameters and the project's anomaly-label convention. The model is
unsupervised: fitting must only ever receive the four feature columns, never
the label.

Parameter note (contamination=0.20): this parameter reflects the known
ground-truth composition of the controlled synthetic dataset (20% attack
rows); it was not discovered by the model and does not represent the model
operating without prior information. It is not tuned to improve results.

Anomaly-label convention (scikit-learn -> project):
scikit-learn returns -1 for anomalies and 1 for normal points. This module
maps -1 -> 1 (flagged/suspicious) and 1 -> 0 (not flagged), matching the
evaluation convention used by the rule-based baseline.
"""

from collections.abc import Iterable

from sklearn.ensemble import IsolationForest

# Feature order used for every model input row. token_reuse_flag is encoded
# as boolean True -> 1, False -> 0; this is the only feature transformation
# applied (no scaling or normalization).
FEATURE_COLUMNS = [
    "geo_velocity_kmh",
    "device_mismatch_score",
    "token_reuse_flag",
    "login_burst_count",
]

# Frozen experimental parameters, fixed before evaluation; not tuned.
CONTAMINATION: float = 0.20
N_ESTIMATORS: int = 100
RANDOM_STATE: int = 42

# scikit-learn -> project prediction convention:
# -1 (anomaly) -> 1 (flagged/suspicious); 1 (normal) -> 0 (not flagged).
ANOMALY_TO_FLAGGED: dict[int, int] = {-1: 1, 1: 0}


def build_model() -> IsolationForest:
    """Instantiate the Isolation Forest with the frozen experiment parameters."""
    return IsolationForest(
        contamination=CONTAMINATION,
        n_estimators=N_ESTIMATORS,
        random_state=RANDOM_STATE,
    )


def feature_row(
    geo_kmh: float,
    device_mismatch: float,
    token_reuse_flag: bool,
    burst_count: int,
) -> list[float]:
    """Build one model input row; bool token flag is encoded True -> 1, False -> 0."""
    return [
        geo_kmh,
        device_mismatch,
        1.0 if token_reuse_flag else 0.0,
        float(burst_count),
    ]


def to_project_predictions(raw_predictions: Iterable[int]) -> list[int]:
    """Convert scikit-learn -1/1 outputs to the project convention 1/0."""
    return [ANOMALY_TO_FLAGGED[int(value)] for value in raw_predictions]
