"""
Hash service — generates GDPR-safe repeat detection tokens.

Uses HMAC-SHA256 (not plain SHA256 concatenation) which is cryptographically
correct: immune to length-extension attacks and ensures the salt cannot be
separated from the message without knowing the key.

Configuration
-------------
The salt must be configured (see ``_configured_salt`` for where). It should
be a cryptographically random string of at least 16 characters (32+
recommended). Once analytics ingestion has run, **never change this value**
— rotating it invalidates every historical repeat hash and silently breaks
all repeat-buyer detection.

If the setting is missing or too short:
  * with DEBUG=True, the service derives an ephemeral salt from SECRET_KEY
    and logs a loud one-time warning. This is fine for local development
    because the data is throwaway.
  * with DEBUG=False, the service raises ImproperlyConfigured and refuses
    to produce a hash. Failing closed prevents a silent misconfiguration
    from polluting production analytics.
"""
import hashlib
import hmac
import logging
import unicodedata

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

_MIN_SALT_LEN = 16
_fallback_warning_emitted = False


def _derive_debug_salt() -> str:
    """Ephemeral dev-only salt — still HMAC-strong, just not stable across
    SECRET_KEY rotation."""
    secret_key = getattr(settings, "SECRET_KEY", "")
    if not secret_key:
        raise ImproperlyConfigured(
            "PRETIX_ANALYTICS_SECRET_SALT is unset and SECRET_KEY is also "
            "empty; cannot derive a dev fallback salt."
        )
    return hmac.new(
        secret_key.encode("utf-8"),
        b"pretix-event-analytics-dev-fallback",
        hashlib.sha256,
    ).hexdigest()


def _configured_salt():
    """
    Look the salt up wherever a Pretix operator can actually put it:

    1. Django setting ``PRETIX_ANALYTICS_SECRET_SALT`` (custom settings module)
    2. ``pretix.cfg``::

           [pretix_event_analytics]
           secret_salt = ...

       or its environment form ``PRETIX_PRETIX_EVENT_ANALYTICS_SECRET_SALT``
    3. environment variable ``PRETIX_ANALYTICS_SECRET_SALT``

    Pretix does not copy arbitrary cfg keys into Django settings, so (2) and
    (3) are what most installations use.
    """
    import os

    salt = getattr(settings, "PRETIX_ANALYTICS_SECRET_SALT", None)
    if salt:
        return salt
    cfg = getattr(settings, "CONFIG_FILE", None)
    if cfg is not None:
        try:
            salt = cfg.get("pretix_event_analytics", "secret_salt", fallback=None)
        except Exception:
            salt = None
        if salt:
            return salt
    return os.environ.get("PRETIX_ANALYTICS_SECRET_SALT")


def _get_salt() -> str:
    """Return the analytics HMAC salt, or raise if none is configured."""
    global _fallback_warning_emitted

    salt = _configured_salt()
    if isinstance(salt, str) and len(salt) >= _MIN_SALT_LEN:
        return salt

    if getattr(settings, "DEBUG", False):
        if not _fallback_warning_emitted:
            logger.warning(
                "PRETIX_ANALYTICS_SECRET_SALT is not configured — using a "
                "SECRET_KEY-derived fallback because DEBUG=True. Set an "
                "explicit salt (32+ random chars) before going to production."
            )
            _fallback_warning_emitted = True
        return _derive_debug_salt()

    raise ImproperlyConfigured(
        "The analytics secret salt is required in production and must be at "
        f"least {_MIN_SALT_LEN} characters. Add it to pretix.cfg as "
        "[pretix_event_analytics] secret_salt = <32+ random characters> "
        "(or set PRETIX_ANALYTICS_SECRET_SALT) and never change it — "
        "rotating invalidates all repeat-detection history."
    )


def generate_repeat_hash(value: str) -> str:
    """
    Generate a deterministic, non-reversible token for repeat buyer detection.

    Empty / None input returns an empty string so the caller can store
    ``""`` as "no hash" instead of letting hundreds of anonymous orders
    collide on the HMAC of the empty string.

    :param value: Email, card fingerprint, IBAN, PayPal payer id, or a
                  composite identity string.
    :returns: 64-character hex digest (HMAC-SHA256), or ``""`` if input
              is empty.
    """
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value).lower().strip()
    if not normalized:
        return ""
    salt = _get_salt()
    return hmac.new(
        salt.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
