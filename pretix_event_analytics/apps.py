from django.apps import AppConfig

from . import PretixPluginMeta as _PluginMeta


class PluginApp(AppConfig):
    name = "pretix_event_analytics"
    verbose_name = "Event Analytics"

    # Expose PretixPluginMeta on the AppConfig so Pretix can discover it
    PretixPluginMeta = _PluginMeta

    def ready(self):
        from . import exporters  # noqa – registers Pretix export-menu exporters
        from . import shredder  # noqa – registers GDPR data shredder
        from . import signals  # noqa – registers signal handlers
