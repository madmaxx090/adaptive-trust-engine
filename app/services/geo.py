"""Live geo-velocity signal: offline GeoLite2 geolocation + haversine distance.

The GeoLite2 database file is placed manually (never auto-downloaded) at the
path configured by ``settings.geoip_db_path``.

Edge-case handling in ``compute_geo_velocity`` (fixed precedence, top-down):

- first-ever session (no previous IP/timestamp) -> 0.0 km/h, "no_history"
- malformed IP string                          -> 0.0 km/h, "invalid_ip"
- private/local/reserved IP (not is_global)    -> 0.0 km/h, "private_ip"
- elapsed time <= 0 (clock skew, duplicates)   -> 0.0 km/h, "invalid_elapsed_time"
- IP missing from GeoLite2                     -> 0.0 km/h, "geolocation_not_found"
- database file missing/unreadable             -> GeoIPDatabaseUnavailableError
- otherwise: haversine distance / elapsed hours, status "ok"

The fallback statuses keep an unmeasurable case (e.g. a private IP) from ever
being confused with a genuine zero-velocity measurement. The velocity is
intentionally uncapped here; the frozen baseline scorer caps only its
normalized contribution at GEO_VELOCITY_CAP_KMH (1000 km/h) and is neither
modified nor duplicated by this module.
"""

import ipaddress
import math
from datetime import datetime

import geoip2.database
import geoip2.errors

from app.core.config import settings

EARTH_RADIUS_KM: float = 6371.0

STATUS_OK: str = "ok"
STATUS_NO_HISTORY: str = "no_history"
STATUS_INVALID_IP: str = "invalid_ip"
STATUS_PRIVATE_IP: str = "private_ip"
STATUS_INVALID_ELAPSED_TIME: str = "invalid_elapsed_time"
STATUS_GEOLOCATION_NOT_FOUND: str = "geolocation_not_found"


class GeoIPDatabaseUnavailableError(Exception):
    """The GeoLite2 database file is missing or unreadable (fail loudly)."""


# Path-keyed lazy cache; a missing/unreadable file raises at the point of use.
_readers: dict[str, geoip2.database.Reader] = {}


def _get_reader() -> geoip2.database.Reader:
    """Return the cached GeoLite2 reader for the configured database path."""
    path = settings.geoip_db_path
    if path not in _readers:
        try:
            _readers[path] = geoip2.database.Reader(path)
        except Exception as exc:  # FileNotFoundError, InvalidDatabaseError, ...
            raise GeoIPDatabaseUnavailableError(
                f"GeoLite2 database unavailable or unreadable at '{path}': {exc}"
            ) from exc
    return _readers[path]


def geolocate(ip: str) -> tuple[float, float] | None:
    """Return (latitude, longitude) for an IP, or None when not in the database."""
    try:
        record = _get_reader().city(ip)
    except geoip2.errors.AddressNotFoundError:
        return None
    except ValueError:
        return None
    latitude, longitude = record.location.latitude, record.location.longitude
    if latitude is None or longitude is None:
        return None
    return latitude, longitude


def _ip_scope(ip: str) -> str:
    """Classify an IP string as 'invalid', 'non_global' or 'global'."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return "invalid"
    return "global" if address.is_global else "non_global"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two coordinates, in kilometers."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    inner = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(inner))


def compute_geo_velocity(
    previous_ip: str | None,
    previous_last_seen_at: datetime | None,
    current_ip: str,
    current_time: datetime,
) -> tuple[float, str, tuple[float, float] | None]:
    """Compute (geo_velocity_kmh, geo_location_status, current_coordinates).

    ``current_coordinates`` is (latitude, longitude) for the current IP when
    the status is "ok", else None (used for the Redis session context label).
    """
    # 1. First-ever session for this user: no history available (not an error).
    if previous_ip is None or previous_last_seen_at is None:
        return 0.0, STATUS_NO_HISTORY, None

    # 2. Malformed IP strings can never be geolocated.
    if _ip_scope(previous_ip) == "invalid" or _ip_scope(current_ip) == "invalid":
        return 0.0, STATUS_INVALID_IP, None

    # 3. Private/local/reserved addresses cannot be geolocated by GeoLite2;
    #    numeric fallback with a distinct status so this is never represented
    #    like a real zero-velocity result.
    if _ip_scope(previous_ip) == "non_global" or _ip_scope(current_ip) == "non_global":
        return 0.0, STATUS_PRIVATE_IP, None

    # 4. Elapsed time must be positive (clock skew / duplicate or out-of-order
    #    requests): explicit guard, so no division by zero and no negative or
    #    infinite velocity can occur.
    elapsed_seconds = (current_time - previous_last_seen_at).total_seconds()
    if elapsed_seconds <= 0:
        return 0.0, STATUS_INVALID_ELAPSED_TIME, None

    # 5. Database access at the point of use: a missing or unreadable file
    #    raises GeoIPDatabaseUnavailableError (never a fabricated value).
    previous_coordinates = geolocate(previous_ip)
    current_coordinates = geolocate(current_ip)

    # 6. Graceful fallback when either IP has no record in the database.
    if previous_coordinates is None or current_coordinates is None:
        return 0.0, STATUS_GEOLOCATION_NOT_FOUND, None

    # 7. Real measurement: haversine distance / elapsed hours. Same/near-same
    #    location yields a near-zero velocity; a stale previous session simply
    #    has a large time denominator. No cap here (the frozen baseline scorer
    #    caps its normalized component instead).
    distance_km = haversine_km(*previous_coordinates, *current_coordinates)
    velocity_kmh = distance_km / (elapsed_seconds / 3600.0)
    return velocity_kmh, STATUS_OK, current_coordinates
