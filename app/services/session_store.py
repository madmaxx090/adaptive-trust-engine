"""Redis-backed fast-access session/token data layer (data access only)."""

from datetime import datetime

import redis

from app.core.config import settings

REFRESH_TOKEN_TTL_SECONDS: int = 86_400  # 24 hours
SESSION_CONTEXT_TTL_SECONDS: int = 86_400  # 24 hours

_client: redis.Redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)


def store_refresh_token_hash(session_id: str, token_hash: str) -> None:
    """Store the current refresh-token hash for a session with a TTL."""
    _client.set(
        f"session:{session_id}:refresh_token_hash",
        token_hash,
        ex=REFRESH_TOKEN_TTL_SECONDS,
    )


def verify_refresh_token_hash(session_id: str, token_hash: str) -> bool:
    """Return whether the incoming refresh-token hash matches the stored one."""
    stored = _client.get(f"session:{session_id}:refresh_token_hash")
    return stored is not None and stored == token_hash


def update_session_context(
    session_id: str,
    device_fingerprint: str,
    location: str,
    last_seen_at: datetime,
) -> None:
    """Refresh the fast-access context (device, location, last seen) for a session."""
    key = f"session:{session_id}:context"
    _client.hset(
        key,
        mapping={
            "device_fingerprint": device_fingerprint,
            "location": location,
            "last_seen_at": last_seen_at.isoformat(),
        },
    )
    _client.expire(key, SESSION_CONTEXT_TTL_SECONDS)
