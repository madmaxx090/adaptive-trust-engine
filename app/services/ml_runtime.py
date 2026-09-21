"""Live ML signal: load-once Isolation Forest inference for /session/score.

The trained model artifact is produced offline by ``train_ml_model.py`` from
the frozen Phase 5 experiment (same dataset, same 80/20 stratified split,
same IsolationForest parameters) and is loaded ONCE per process: at FastAPI
startup via the application lifespan, with a lock-guarded lazy first-use
fallback for ASGI hosts and test clients that do not run lifespan events.

Contract notes:
- The feature row is built with the frozen ``feature_row`` helper, so the
  order [geo_velocity_kmh, device_mismatch_score, token_reuse_flag,
  login_burst_count] and the True -> 1.0 / False -> 0.0 encoding are
  identical to the Phase 5 training/evaluation code by construction.
- The anomaly flag uses the frozen sklearn convention -1 (anomaly) -> 1
  (flagged), 1 -> 0, exactly the conversion the Phase 5 evaluation applied.
- ``decision_score`` is the raw Isolation Forest decision_function output:
  higher = more normal, negative = anomalous. It is uncalibrated (not a
  probability) and no 0-100 conversion is applied: the ML signal is exposed
  alongside, never fused with, the rule-based baseline score.
- No offline evaluation metric is claimed for the live system here.

Failure policy: a missing/corrupt artifact, invalid feature values, or an
unexpected inference error raises ``MLSignalError``. Startup fails fast with
a descriptive message; at request time the API maps it to the fixed generic
503 body (full detail logged server-side) -- never a silent fallback
pretending the ML signal ran.
"""

import math
import threading

import joblib
from sklearn.ensemble import IsolationForest

from app.core.config import settings
from app.services.isolation_forest_scorer import feature_row, to_project_predictions


class MLSignalError(Exception):
    """ML signal unavailable: missing/corrupt artifact or invalid features."""


class IsolationForestRuntime:
    """Loads the model artifact once (thread-safe) and scores sessions."""

    def __init__(self) -> None:
        self._model: IsolationForest | None = None
        self._path: str | None = None
        self._lock = threading.Lock()

    def load(self, path: str) -> None:
        """Load the model artifact from ``path`` (idempotent, thread-safe)."""
        with self._lock:
            if self._model is not None and self._path == path:
                return
            try:
                model = joblib.load(path)
            except Exception as exc:
                raise MLSignalError(
                    f"ML model artifact unavailable or unreadable at '{path}': {exc}"
                ) from exc
            if not isinstance(model, IsolationForest):
                raise MLSignalError(
                    f"ML model artifact at '{path}' is not an IsolationForest "
                    f"instance (got {type(model).__name__})."
                )
            self._model = model
            self._path = path

    def ensure_loaded(self) -> None:
        """Lazy fallback: load on first use when startup did not run (tests)."""
        if self._model is None:
            self.load(settings.ml_model_path)

    def score(
        self,
        geo_kmh: float,
        device_mismatch: float,
        token_reuse_flag: bool,
        burst_count: int,
    ) -> tuple[bool, float]:
        """Return (anomaly_flag, decision_score) for one session's signals.

        The flag is True for an anomaly (project convention), otherwise False;
        the decision score is the raw, uncalibrated sklearn output.
        """
        self._validate_features(
            geo_kmh, device_mismatch, token_reuse_flag, burst_count
        )
        self.ensure_loaded()
        row = feature_row(geo_kmh, device_mismatch, token_reuse_flag, burst_count)
        try:
            flagged = bool(to_project_predictions(self._model.predict([row]))[0])
            decision_score = float(self._model.decision_function([row])[0])
        except Exception as exc:
            raise MLSignalError(f"ML inference failed: {exc}") from exc
        return flagged, decision_score

    @staticmethod
    def _validate_features(
        geo_kmh: float,
        device_mismatch: float,
        token_reuse_flag: bool,
        burst_count: int,
    ) -> None:
        numeric = (
            ("geo_velocity_kmh", geo_kmh),
            ("device_mismatch_score", device_mismatch),
            ("login_burst_count", burst_count),
        )
        for name, value in numeric:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MLSignalError(
                    f"Non-numeric feature value for {name}: {value!r}"
                )
            if not math.isfinite(float(value)):
                raise MLSignalError(
                    f"Non-finite feature value for {name}: {value!r}"
                )
        if not isinstance(token_reuse_flag, bool):
            raise MLSignalError(
                f"token_reuse_flag must be a bool, got {token_reuse_flag!r}"
            )


# Process-wide singleton; loaded once at startup (see app/main.py lifespan).
ml_runtime = IsolationForestRuntime()
