from django.utils.translation import gettext_lazy as _

try:
    from pretix.base.plugins import PluginConfig  # noqa
except ImportError:
    raise RuntimeError("Please use pretix in INSTALLED_APPS")


class PretixPluginMeta:
    name = _("Event Analytics")
    author = "Andre Vidal"
    version = "1.2.0"
    visible = True
    restricted = False
    description = _(
        "Advanced event-level analytics with cross-edition repeat buyer tracking, "
        "cohort retention matrix, predictive repeat scoring, and a full analytics dashboard."
    )
    category = "FEATURE"
    compatibility = "pretix>=2025.0.0"
