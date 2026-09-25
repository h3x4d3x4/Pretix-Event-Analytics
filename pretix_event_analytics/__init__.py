from django.utils.translation import gettext_lazy as _

try:
    from pretix.base.plugins import PluginConfig  # noqa
except ImportError:
    raise RuntimeError("Please use pretix in INSTALLED_APPS")


class PretixPluginMeta:
    name = _("Event Analytics")
    author = "Andre Vidal"
    version = "2.0.0"
    visible = True
    restricted = False
    description = _(
        "Analytics suite: sales against previous editions, first-timers and returning people "
        "across every edition, audience, tickets, check-in and refunds — without storing personal data."
    )
    category = "FEATURE"
    compatibility = "pretix>=2025.0.0"
