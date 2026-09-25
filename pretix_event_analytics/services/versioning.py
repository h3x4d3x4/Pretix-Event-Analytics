"""
Cache versioning for analytics read models.

Every write to an organizer's analytics tables bumps a per-organizer
version number. Read caches (dashboard sections, attendance sets, cohort
matrices) include the version in their key, so they invalidate themselves
without having to enumerate keys.
"""
import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_TTL = 60 * 30


def _key(organizer_id: int) -> str:
    return f"pretix_analytics:v:{organizer_id}"


def data_version(organizer_id: int) -> int:
    try:
        v = cache.get(_key(organizer_id))
        if v is None:
            v = 1
            cache.set(_key(organizer_id), v, None)
        return v
    except Exception:
        logger.debug("analytics: cache version read failed", exc_info=True)
        return 0


def bump(organizer_id: int) -> None:
    try:
        cache.incr(_key(organizer_id))
    except ValueError:
        cache.set(_key(organizer_id), 2, None)
    except Exception:
        logger.debug("analytics: cache version bump failed", exc_info=True)


def cached(organizer_id: int, key: str, fn, ttl: int = CACHE_TTL):
    """Return fn() memoised under (organizer version, key)."""
    full = f"pretix_analytics:c:{organizer_id}:{data_version(organizer_id)}:{key}"
    try:
        hit = cache.get(full)
        if hit is not None:
            return hit
    except Exception:
        logger.debug("analytics: cache read failed", exc_info=True)
    value = fn()
    try:
        cache.set(full, value, ttl)
    except Exception:
        logger.debug("analytics: cache write failed", exc_info=True)
    return value
