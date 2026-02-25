"""
Hash service — generates GDPR-safe repeat detection tokens.

Uses HMAC-SHA256 (not plain SHA256 concatenation) which is cryptographically
correct: immune to length-extension attacks and ensures the salt cannot be
separated from the message without knowing the key.

PRETIX_ANALYTICS_SECRET_SALT must be set in Django settings.
It should be a long random string (32+ chars) and never changed once
orders are ingested — changing it invalidates all repeat detection history.
"""
import hashlib
import hmac

from django.conf import settings


def _get_salt() -> str:
    """
    Return the analytics HMAC salt.

    Uses PRETIX_ANALYTICS_SECRET_SALT if explicitly set.
    Falls back to deriving a stable salt from Django's SECRET_KEY.
    """
    salt = getattr(settings, "PRETIX_ANALYTICS_SECRET_SALT", None)
    if salt and isinstance(salt, str) and len(salt) >= 16:
        return salt
    # Derive a stable salt from SECRET_KEY so repeat detection works
    # even without explicit configuration.
    secret_key = getattr(settings, "SECRET_KEY", "")
    if not secret_key:
        raise RuntimeError(
            "Neither PRETIX_ANALYTICS_SECRET_SALT nor SECRET_KEY is configured."
        )
    return hmac.new(
        b"pretix-event-analytics-salt",
        secret_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_repeat_hash(email: str) -> str:
    """
    Generate a deterministic, non-reversible token for repeat buyer detection.

    :param email: Raw order email address or identity string.
    :returns: 64-character hex digest (HMAC-SHA256).
    """
    salt = _get_salt()
    normalized = email.lower().strip()
    digest = hmac.new(
        salt.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest
