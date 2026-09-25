"""
Compatibility with Pretix's permission model.

Pretix 2026.3 replaced boolean team permissions (``can_view_orders``) with
permission groups (``event.orders:read``). Legacy names still work there but
are deprecated. ``perm()`` returns the name the running Pretix expects.
"""
LEGACY_TO_NEW = {
    "can_view_orders": "event.orders:read",
    "can_change_event_settings": "event.settings.general:write",
    "can_change_organizer_settings": "organizer.settings.general:write",
}


def _has_permission_groups() -> bool:
    try:
        from pretix.base import permissions  # noqa: F401  (introduced in 2026.3)
    except ImportError:
        return False
    return True


NEW_PERMISSIONS = _has_permission_groups()


def perm(legacy_name: str) -> str:
    return LEGACY_TO_NEW[legacy_name] if NEW_PERMISSIONS else legacy_name


VIEW_ORDERS = perm("can_view_orders")
CHANGE_EVENT_SETTINGS = perm("can_change_event_settings")
CHANGE_ORGANIZER_SETTINGS = perm("can_change_organizer_settings")
