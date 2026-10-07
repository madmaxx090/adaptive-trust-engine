"""Cross-account cohort signal: shared device / shared token across user_ids.

Every other signal in the pipeline is *per-user*: it compares this request
against the same user's own history (previous session, per-user baseline) or
against this user's own attempt rate (login burst). None of them can see that
the SAME device fingerprint or the SAME refresh-token hash is turning up under
several DIFFERENT user_ids -- the credential-stuffing and account-farming
pattern, where one operator drives many accounts. This module looks sideways
across accounts instead of backwards through one account.

Additive-only signal, integrated exactly like the ML signal
(``app/services/ml_runtime.py``) and the per-user baseline
(``app/services/user_baseline.py``): computed separately from the frozen
rule-based scorer, exposed alongside it in ``contributing_signals``, and NEVER
fused into the frozen score. ``app/services/baseline_scorer.py`` is not imported
here and its weights are untouched.

Storage: one Redis sorted set per fingerprint and per token hash, keyed
``ate:cohort:device:{fingerprint}`` / ``ate:cohort:token:{sha256}``, with the
user_id as the member and the observation time as the score. A sorted set rather
than a plain SET for two reasons:

- ``ZADD`` on an existing member UPDATES its score instead of adding a
  duplicate, so the cardinality is the count of DISTINCT user_ids for free --
  exactly the quantity this signal reports. A SET would give that too, but...
- ...only a sorted set supports a genuine ROLLING window: pruning
  ``score <= now - 24h`` drops each member on its own age. A SET can only carry
  one TTL for the whole key, and because the TTL is refreshed on every write an
  active fingerprint's key would never expire, so stale user_ids would linger
  and inflate the count forever.

This is the same idiom as the login-burst counter in ``risk_pipeline``
(``zremrangebyscore`` + ``zcard`` + ``zadd`` + ``expire`` in one transactional
pipeline), applied over a 24-hour window instead of 60 seconds.

Ordering: the count is read BEFORE this request's user_id is written -- the
same principle the per-user baseline follows by running before persistence --
but the reported number is the distinct-account cardinality WITH this request
included, so one account alone always reports 1 and a shared device or token
reports more. Getting that right needs one extra step: because the read happens
first, the caller's own EARLIER sightings are already members of the set, so a
raw cardinality would report the user as their own cohort peer (and, under
concurrent requests from one account, would race between two values). The
caller's own membership is therefore subtracted before the one is added back,
which makes the result independent of write ordering.

Like the burst counter, the write happens before the Postgres commit, so an
attempt is recorded even if persistence later fails. That is deliberate: a
failed login from a device already used by other accounts is the evidence this
signal exists to surface, and discarding it on a database error would hide
exactly the interesting case.

Tokens: only the SHA-256 hash the pipeline already computed is ever used, as a
key. No raw refresh token reaches Redis (same rule as ``session_store``).

Failure policy: a Redis failure raises ``CohortSignalUnavailableError``, which
the API maps to the fixed generic 503 -- never a silent fallback pretending the
cohort check ran. This matches the other additive signals; note that a Redis
outage already fails the request earlier (burst counting and token-reuse
verification both raise), so this path is a narrow window rather than a new
failure mode.
"""

import time
from dataclasses import dataclass

import redis

from app.core.config import settings

# Rolling observation window. The key TTL equals the window because any member
# older than the window is pruned on the next read anyway, so a key that has not
# been written for a full window holds nothing worth keeping.
COHORT_WINDOW_SECONDS: int = 86_400  # 24 hours
COHORT_KEY_TTL_SECONDS: int = 86_400  # 24 hours

# Status vocabulary mirrors app/services/geo.py and app/services/user_baseline.py.
STATUS_OK: str = "ok"

DEVICE_KEY_PREFIX: str = "ate:cohort:device:"
TOKEN_KEY_PREFIX: str = "ate:cohort:token:"

_client: redis.Redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)


class CohortSignalUnavailableError(Exception):
    """Redis unavailable while computing the cross-account cohort signal."""


@dataclass(frozen=True)
class CohortSignal:
    """Distinct accounts seen on this device fingerprint / with this token hash.

    Both counts INCLUDE the current request, so 1 means "this account alone"
    and anything above 1 means the device or the token is being shared across
    accounts.

    ``status`` is ``"ok"`` whenever this object exists: a Redis failure raises
    instead of returning a degraded result (see module docstring), so there is
    no "unavailable" value to report here. It is kept for symmetry with
    ``UserBaseline.status`` and so callers can assert the signal really ran.

    ``token_cohort_user_count`` is None when the request carried no refresh
    token -- nothing was observable, which is different from observing one
    account. ``device_cohort_user_count`` is always an int because a fingerprint
    is a required field on every scoring request.
    """

    status: str
    device_cohort_user_count: int
    token_cohort_user_count: int | None


def compute_cohort_signal(
    user_id: str,
    device_fingerprint: str,
    token_hash: str | None,
) -> CohortSignal:
    """Count distinct accounts sharing this device fingerprint / token hash.

    Must be called BEFORE the current session is persisted (and it records this
    request itself, so the next request sees it).
    """
    device_count = _count_cohort_and_record(
        f"{DEVICE_KEY_PREFIX}{device_fingerprint}", user_id
    )
    token_count = (
        None
        if token_hash is None
        else _count_cohort_and_record(f"{TOKEN_KEY_PREFIX}{token_hash}", user_id)
    )
    return CohortSignal(
        status=STATUS_OK,
        device_cohort_user_count=device_count,
        token_cohort_user_count=token_count,
    )


def _count_cohort_and_record(key: str, user_id: str) -> int:
    """Distinct accounts on ``key`` inside the rolling window, this one included.

    One transactional pipeline: prune members older than the window, read the
    cardinality and whether this user is already a member, then record this
    request and refresh the key TTL. Subtracting the caller's own membership
    before adding one back makes the result independent of write ordering, so
    concurrent requests from a single account all report 1 instead of racing.
    """
    now_ts = time.time()
    try:
        with _client.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, "-inf", now_ts - COHORT_WINDOW_SECONDS)
            pipe.zcard(key)
            pipe.zscore(key, user_id)
            pipe.zadd(key, {user_id: now_ts})
            pipe.expire(key, COHORT_KEY_TTL_SECONDS)
            results = pipe.execute()
    except redis.RedisError as exc:
        raise CohortSignalUnavailableError(
            f"Redis unavailable during cross-account cohort computation: {exc}"
        ) from exc
    cardinality = int(results[1])
    already_a_member = results[2] is not None
    return cardinality - (1 if already_a_member else 0) + 1
