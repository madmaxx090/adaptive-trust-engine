"""Per-user adaptive baseline: historical aggregates for a single user.

Additive-only signal, integrated exactly like the ML signal
(``app/services/ml_runtime.py``): computed separately from the frozen
rule-based scorer, exposed alongside it in ``contributing_signals``, and NEVER
fused into the frozen score. ``app/services/baseline_scorer.py`` is not
imported here and its weights are untouched.

Everything is derived from data the pipeline already persists -- no new tables
and no schema change:

- ``sessions.device_fingerprint``      -> how often this exact device was seen
- ``risk_events.contributing_signals`` -> the user's measured geo-velocity
  history, used to rank the current measurement

Only velocities recorded with ``geo_location_status == "ok"`` count as history.
``geo.compute_geo_velocity`` deliberately reports 0.0 for unmeasurable cases
(``no_history``, ``private_ip``, ``invalid_ip``, ``geolocation_not_found``), and
those placeholder zeros must not be mistaken for real measurements -- the same
distinction the geo module documents for its own output.

Cold start: a user with no prior sessions yields status ``"no_history"`` (the
status vocabulary mirrors ``app/services/geo.py``), a zero device count and no
velocity percentile, rather than numbers that would imply a measured baseline
exists.

Failure policy: a database failure raises ``UserBaselineUnavailableError``,
which the API maps to the fixed generic 503 -- never a silent fallback
pretending a baseline was computed.
"""

from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.models import RiskEvent, Session, User
from app.services import geo

# Status vocabulary mirrors app/services/geo.py.
STATUS_OK: str = "ok"
STATUS_NO_HISTORY: str = "no_history"


class UserBaselineUnavailableError(Exception):
    """PostgreSQL unavailable while computing the per-user baseline."""


@dataclass(frozen=True)
class UserBaseline:
    """One user's historical baseline, as of just before the current session.

    ``geo_velocity_user_percentile`` is None when the user has history but none
    of it is a measured velocity (e.g. every prior session was from a private
    IP), which is distinct from ``status == "no_history"``.
    """

    status: str
    device_seen_before_count: int
    geo_velocity_user_percentile: float | None


def compute_user_baseline(
    user_id: str,
    device_fingerprint: str,
    current_velocity_kmh: float,
) -> UserBaseline:
    """Aggregate one user's prior sessions into an adaptive baseline.

    Must be called BEFORE the current session is persisted, so the request
    never contributes to its own baseline.
    """
    try:
        with SessionLocal() as db:
            user_pk = db.execute(
                select(User.id).where(User.user_id == user_id)
            ).scalar_one_or_none()
            if user_pk is None:
                return _no_history()

            prior_sessions, same_device = db.execute(
                select(
                    func.count(),
                    func.sum(
                        case(
                            (Session.device_fingerprint == device_fingerprint, 1),
                            else_=0,
                        )
                    ),
                ).where(Session.user_id == user_pk)
            ).one()

            if prior_sessions == 0:
                return _no_history()

            measured_velocities = list(
                db.execute(
                    select(
                        RiskEvent.contributing_signals["geo_velocity_kmh"].as_float()
                    )
                    .join(Session, RiskEvent.session_id == Session.id)
                    .where(Session.user_id == user_pk)
                    .where(
                        RiskEvent.contributing_signals[
                            "geo_location_status"
                        ].as_string()
                        == geo.STATUS_OK
                    )
                ).scalars()
            )
    except (SQLAlchemyError, ValueError) as exc:
        # Same normalization as risk_pipeline._load_previous_session: psycopg2
        # rejects NUL bytes in text parameters client-side with a plain
        # ValueError that SQLAlchemy re-raises unwrapped, so both classes are
        # handled here and reach the sanitized-503 path.
        raise UserBaselineUnavailableError(
            f"PostgreSQL unavailable during per-user baseline computation: {exc}"
        ) from exc

    return UserBaseline(
        status=STATUS_OK,
        device_seen_before_count=int(same_device or 0),
        geo_velocity_user_percentile=_percentile_at_or_below(
            measured_velocities, current_velocity_kmh
        ),
    )


def _no_history() -> UserBaseline:
    """Cold start: no prior sessions, so nothing is measurable."""
    return UserBaseline(
        status=STATUS_NO_HISTORY,
        device_seen_before_count=0,
        geo_velocity_user_percentile=None,
    )


def _percentile_at_or_below(
    historical: list[float], current: float
) -> float | None:
    """Percentile rank (0-100) of ``current`` within ``historical``.

    The share of prior measured velocities at or below the current one, so a
    value far above the user's history ranks near 100. None when there is no
    measured velocity history to rank against.
    """
    if not historical:
        return None
    at_or_below = sum(1 for value in historical if value <= current)
    return round(100.0 * at_or_below / len(historical), 2)
