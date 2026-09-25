from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

from . import PretixPluginMeta as _PluginMeta


class PluginApp(AppConfig):
    name = "pretix_event_analytics"
    verbose_name = "Event Analytics"

    # Expose PretixPluginMeta on the AppConfig so Pretix can discover it
    PretixPluginMeta = _PluginMeta

    def ready(self):
        from . import signals  # noqa – registers signal handlers
        from . import shredder  # noqa – registers GDPR data shredder
        from . import exporters  # noqa – registers Pretix export-menu exporters
