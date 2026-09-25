"""
GDPR data shredder for the analytics plugin.

Pretix requires plugins that store user-related data to implement a
BaseDataShredder so organisers can fulfill GDPR data deletion requests.

Although this plugin only stores HMAC-SHA256 hashes (not raw PII), those
hashes are pseudonymous identifiers under GDPR Article 4 and must be
deletable on request.

Shredding removes all AnalyticsOrderFact, AnalyticsTicketFact, and
AnalyticsIdentity records for the event. The event's cohort cache is
also invalidated.
"""
import json
from typing import List, Tuple

from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from pretix.base.shredder import BaseDataShredder
from pretix.base.signals import register_data_shredders


class AnalyticsDataShredder(BaseDataShredder):
    verbose_name = _("Analytics data (hashed buyer identities, order facts)")
    identifier = "pretix_event_analytics"
    description = _(
        "This will remove all analytics fact records for this event, including "
        "HMAC-hashed buyer identities used for repeat detection, order-level "
        "aggregates, and ticket-level breakdowns. Cohort retention data that "
        "depends on this event will also be affected."
    )

    def generate_files(self) -> List[Tuple[str, str, str]]:
        from .models import AnalyticsOrderFact

        facts = AnalyticsOrderFact.objects.filter(event=self.event)
        if not facts.exists():
            return []

        rows = list(
            facts.values(
                "order_code", "repeat_hash", "country_code", "city",
                "postal_code", "age_range", "language",
            )
        )
        return [
            (
                "analytics_data.json",
                "application/json",
                json.dumps(rows, indent=2, default=str),
            )
        ]

    def shred_data(self, progress_callback=None):
        from .models import AnalyticsOrderFact, EventAnalyticsConfig

        qs = AnalyticsOrderFact.objects.filter(event=self.event)

        # Cascade deletes AnalyticsTicketFact and AnalyticsIdentity
        deleted_count, _ = qs.delete()

        # Other editions may have counted these buyers as returning — re-resolve.
        config = EventAnalyticsConfig.objects.select_related("series").filter(event=self.event).first()
        if config and config.series:
            from .services.people import queue_recompute
            queue_recompute(self.event.organizer_id, config.series.slug)
        else:
            from .services.versioning import bump
            bump(self.event.organizer_id)

        if progress_callback:
            progress_callback(100)


@receiver(register_data_shredders, dispatch_uid="pretix_analytics_shredder")
def register_shredder(sender, **kwargs):
    return AnalyticsDataShredder
