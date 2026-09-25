from django.db import models
from django.utils.translation import gettext_lazy as _

# Bump when the ingestion pipeline starts writing new/changed fact fields.
FACT_VERSION = 2


class EventSeries(models.Model):
    """
    Groups multiple Pretix events into a named recurring series
    (e.g. "Suti Festival" → 2022, 2023, 2024, 2026 editions).
    Owned at organizer level so the same organizer can manage multiple series.
    """
    organizer = models.ForeignKey(
        "pretixbase.Organizer",
        on_delete=models.CASCADE,
        related_name="analytics_series",
    )
    name = models.CharField(max_length=200, verbose_name=_("Series name"))
    slug = models.SlugField(
        max_length=200,
        verbose_name=_("Slug"),
        help_text=_("Short unique identifier, e.g. suti-festival"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organizer", "slug"],
                name="unique_organizer_series_slug",
            )
        ]
        indexes = [
            models.Index(fields=["organizer", "slug"]),
        ]
        verbose_name = _("Event Series")
        verbose_name_plural = _("Event Series")

    def __str__(self):
        return self.name


class EventAnalyticsConfig(models.Model):
    """
    Per-event configuration that links a Pretix event to a series and assigns
    its edition year.  Must be configured before analytics data is collected.
    """
    event = models.OneToOneField(
        "pretixbase.Event",
        on_delete=models.CASCADE,
        related_name="analytics_config",
    )
    series = models.ForeignKey(
        EventSeries,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="event_configs",
        verbose_name=_("Series"),
    )
    edition_year = models.IntegerField(
        db_index=True,
        verbose_name=_("Edition year"),
        help_text=_("Calendar year of this edition, e.g. 2024"),
    )
    # Home country used for the 'local buyer' scoring factor.
    # Set to the country where the event takes place.
    home_country = models.CharField(
        max_length=2,
        blank=True,
        verbose_name=_("Home country (ISO alpha-2)"),
        help_text=_("Country code of the event venue, used in repeat probability scoring."),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Include in analytics"),
        help_text=_("Uncheck to exclude this edition from cohort calculations."),
    )
    # Question ids whose (choice / yes-no) answers are aggregated on the
    # dashboard. Opt-in only: answers can describe sensitive categories
    # (health, diet, accessibility), so nothing is collected by default.
    tracked_question_ids = models.JSONField(default=list, blank=True)
    ticket_target = models.PositiveIntegerField(
        null=True, blank=True,
        verbose_name=_("Ticket target"),
        help_text=_("Optional goal shown on the sales forecast, e.g. venue capacity."),
    )
    revenue_target = models.DecimalField(
        max_digits=13, decimal_places=2, null=True, blank=True,
        verbose_name=_("Revenue target"),
        help_text=_("Optional revenue goal shown on the sales forecast."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Event Analytics Config")
        verbose_name_plural = _("Event Analytics Configs")

    def __str__(self):
        series_name = self.series.name if self.series else "—"
        return f"{series_name} {self.edition_year} ({self.event})"


class AnalyticsOrderFact(models.Model):
    """
    Denormalized fact table — one row per order.
    All dashboard queries run against this table only.
    No raw PII stored: email is hashed, birthdates converted to bucket,
    names and ID numbers never stored.
    """

    # ── Event context ────────────────────────────────────────────────────────
    organizer_id = models.IntegerField(db_index=True)
    event = models.ForeignKey(
        "pretixbase.Event",
        on_delete=models.CASCADE,
        related_name="analytics_order_facts",
    )
    series_slug = models.CharField(max_length=200, db_index=True, blank=True)
    edition_year = models.IntegerField(db_index=True, null=True, blank=True)

    # ── Order core ───────────────────────────────────────────────────────────
    order_code = models.CharField(max_length=50, db_index=True)
    order_datetime = models.DateTimeField(db_index=True)
    payment_datetime = models.DateTimeField(null=True, blank=True)
    ORDER_STATUS_CHOICES = [
        ("n", _("Pending")),
        ("p", _("Paid")),
        ("e", _("Expired")),
        ("c", _("Canceled")),
    ]
    order_status = models.CharField(max_length=1, choices=ORDER_STATUS_CHOICES)

    # ── Financials ───────────────────────────────────────────────────────────
    total_gross = models.DecimalField(max_digits=13, decimal_places=2, default=0)
    total_net = models.DecimalField(max_digits=13, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=13, decimal_places=2, default=0)
    fees_total = models.DecimalField(max_digits=13, decimal_places=2, default=0)
    refunded_amount = models.DecimalField(max_digits=13, decimal_places=2, default=0)
    currency = models.CharField(max_length=10, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)

    # ── Order structure ──────────────────────────────────────────────────────
    ticket_count = models.IntegerField(default=0)
    unique_attendee_count = models.IntegerField(default=0)
    is_group_order = models.BooleanField(default=False)
    has_addons = models.BooleanField(default=False)
    addon_count = models.IntegerField(default=0)
    voucher_used = models.BooleanField(default=False)
    # Whole days between the order and the event start, in the event's
    # timezone. Negative for orders placed after the event started.
    days_before_event = models.IntegerField(null=True, blank=True)
    payment_provider = models.CharField(max_length=100, blank=True, db_index=True)
    is_refunded = models.BooleanField(default=False)

    # ── Geography ────────────────────────────────────────────────────────────
    country_code = models.CharField(max_length=2, blank=True, db_index=True)
    city = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)

    # ── Demographics ─────────────────────────────────────────────────────────
    # Age resolved from "Birth Date" question answer, stored as bucket only.
    age_range = models.CharField(max_length=10, blank=True)
    # True if the buyer answered "I confirm I am 18+" affirmatively.
    is_age_confirmed = models.BooleanField(null=True, blank=True)
    language = models.CharField(max_length=10, blank=True)

    # ── Caravan / camping ────────────────────────────────────────────────────
    # True if any position in the order is for a caravan-pass product.
    has_caravan_pass = models.BooleanField(default=False)
    # Bucketed from "How long is your camper van?" — '<6m', '6-8m', '>8m'
    # Blank if no caravan pass or length not provided.
    camper_van_length_bucket = models.CharField(max_length=10, blank=True)

    # ── Repeat buyer detection ───────────────────────────────────────────────
    # HMAC-SHA256(email, SECRET_SALT) — never store raw email
    repeat_hash = models.CharField(max_length=64, db_index=True, blank=True)
    is_repeat_buyer = models.BooleanField(default=False, db_index=True)
    repeat_from_last_edition = models.BooleanField(default=False)
    repeat_from_any_previous = models.BooleanField(default=False)
    repeat_count = models.IntegerField(default=0)
    first_seen_edition_year = models.IntegerField(null=True, blank=True)
    # Stable pseudonymous key of the buyer, shared by every order/ticket that
    # the identity resolver links to the same person within a series.
    # Blank when the buyer carries no usable identity signal.
    person_key = models.CharField(max_length=64, blank=True, default="", db_index=True)
    # Number of series editions this person took part in (including this one).
    editions_attended = models.IntegerField(default=1)
    is_local_buyer = models.BooleanField(default=False)

    # ── Check-in ─────────────────────────────────────────────────────────────
    checkin_completed = models.BooleanField(default=False)

    # ── Prediction ───────────────────────────────────────────────────────────
    predicted_repeat_probability = models.IntegerField(default=0)

    # Ingestion pipeline version that produced this row. Rows older than
    # FACT_VERSION lack fields that newer dashboards rely on; the dashboard
    # asks for a resync when it finds any.
    fact_version = models.PositiveSmallIntegerField(default=1)

    # ── Timestamps ───────────────────────────────────────────────────────────
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "order_code"],
                name="unique_event_order_code",
            ),
        ]
        indexes = [
            models.Index(fields=["event"]),
            models.Index(fields=["repeat_hash"]),
            models.Index(fields=["country_code"]),
            models.Index(fields=["order_datetime"]),
            models.Index(fields=["event", "repeat_hash"]),
            models.Index(fields=["event", "edition_year"]),
            models.Index(fields=["event", "country_code"]),
            models.Index(fields=["event", "order_status"]),
            models.Index(fields=["series_slug", "edition_year"]),
            models.Index(fields=["organizer_id", "series_slug", "edition_year"]),
        ]
        verbose_name = _("Analytics Order Fact")
        verbose_name_plural = _("Analytics Order Facts")

    def __str__(self):
        return f"Order {self.order_code} ({self.event})"


class AnalyticsTicketFact(models.Model):
    """
    Fact table for individual ticket (OrderPosition) level analytics.
    Supports ticket type breakdown, revenue per ticket, add-on analysis.
    No PII stored.
    """
    order_fact = models.ForeignKey(
        AnalyticsOrderFact,
        on_delete=models.CASCADE,
        related_name="ticket_facts",
    )
    event = models.ForeignKey(
        "pretixbase.Event",
        on_delete=models.CASCADE,
        related_name="analytics_ticket_facts",
    )

    # Pretix OrderPosition pk — one row per position.
    position_id = models.IntegerField(null=True, blank=True)

    # Item info — store name as snapshot (item may change later)
    item_id = models.IntegerField(db_index=True)
    item_name = models.CharField(max_length=255)
    item_category = models.CharField(max_length=255, blank=True, default="")
    variation_id = models.IntegerField(null=True, blank=True)
    variation_name = models.CharField(max_length=255, blank=True)

    # Financials
    price = models.DecimalField(max_digits=13, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    net_price = models.DecimalField(max_digits=13, decimal_places=2, default=0)

    # Structure
    is_addon = models.BooleanField(default=False)
    voucher_code = models.CharField(max_length=255, blank=True, default="")
    voucher_tag = models.CharField(max_length=255, blank=True, default="")

    # Check-in (successful entry scans only)
    checked_in = models.BooleanField(default=False)
    first_checkin_at = models.DateTimeField(null=True, blank=True)

    # Attendee identity (admission positions only; add-ons stay blank)
    attendee_person_key = models.CharField(max_length=64, blank=True, default="", db_index=True)
    attendee_identified = models.BooleanField(default=False)
    # True when this ticket is held by the buyer themself.
    attendee_is_buyer = models.BooleanField(default=False)
    is_returning_attendee = models.BooleanField(default=False)
    attendee_previous_editions = models.IntegerField(default=0)
    attendee_first_seen_year = models.IntegerField(null=True, blank=True)
    attendee_editions_attended = models.IntegerField(default=1)

    # Attendee demographics (per-ticket, no PII)
    age_range = models.CharField(max_length=10, blank=True)
    is_age_confirmed = models.BooleanField(null=True, blank=True)

    # Caravan (applicable only on caravan pass products)
    is_caravan_pass = models.BooleanField(default=False)
    camper_van_length_bucket = models.CharField(max_length=10, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order_fact", "position_id"],
                condition=models.Q(position_id__isnull=False),
                name="unique_ticket_per_position",
            ),
        ]
        indexes = [
            models.Index(fields=["event", "item_id"]),
            models.Index(fields=["order_fact"]),
            models.Index(fields=["event", "is_addon"]),
        ]
        verbose_name = _("Analytics Ticket Fact")
        verbose_name_plural = _("Analytics Ticket Facts")

    def __str__(self):
        return f"{self.item_name} — {self.order_fact.order_code}"


class AnalyticsIdentity(models.Model):
    """
    Advanced Resolution Engine table to track returning buyers across
    events using composite demographics or payment fingerprints.
    """
    order_fact = models.ForeignKey(
        AnalyticsOrderFact,
        on_delete=models.CASCADE,
        related_name="identities",
    )
    event = models.ForeignKey(
        "pretixbase.Event",
        on_delete=models.CASCADE,
        related_name="analytics_identities",
    )
    # Set for attendee-level signals (attendee email, name+DOB); null for
    # buyer-level signals (order email, payment fingerprints).
    ticket_fact = models.ForeignKey(
        AnalyticsTicketFact,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="identities",
    )

    # ── Identity Type ────────────────────────────────────────────────────────
    # Types: 'email', 'stripe_card', 'paypal_payer', 'bank_iban', 'name_dob'
    identity_type = models.CharField(max_length=32, db_index=True)
    
    # HMAC-SHA256 representation of the value — NO PII STORED
    identity_hash = models.CharField(max_length=64, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order_fact", "identity_type", "identity_hash"],
                name="unique_identity_per_order",
            ),
        ]
        indexes = [
            models.Index(fields=["event", "identity_type", "identity_hash"]),
        ]
        verbose_name = _("Analytics Identity")
        verbose_name_plural = _("Analytics Identities")

    def __str__(self):
        return f"{self.identity_type} — {self.order_fact.order_code}"


class AnalyticsAnswerFact(models.Model):
    """
    Aggregatable answer to an opt-in choice or yes/no question.
    One row per selected option. Free-text answers are never stored.
    """
    ticket_fact = models.ForeignKey(
        AnalyticsTicketFact,
        on_delete=models.CASCADE,
        related_name="answers",
    )
    event = models.ForeignKey(
        "pretixbase.Event",
        on_delete=models.CASCADE,
        related_name="analytics_answer_facts",
    )
    question_id = models.IntegerField()
    question_label = models.CharField(max_length=255)
    answer_value = models.CharField(max_length=255)

    class Meta:
        indexes = [
            models.Index(fields=["event", "question_id"]),
        ]
        verbose_name = _("Analytics Answer Fact")
        verbose_name_plural = _("Analytics Answer Facts")


class LegacyEdition(models.Model):
    """
    A past edition that predates Pretix (or this plugin), reconstructed from
    an uploaded attendee e-mail list. Only HMAC hashes are kept; the list
    itself is discarded after import.
    """
    series = models.ForeignKey(
        EventSeries,
        on_delete=models.CASCADE,
        related_name="legacy_editions",
    )
    label = models.CharField(max_length=100, verbose_name=_("Label"))
    edition_year = models.IntegerField(verbose_name=_("Edition year"))
    is_active = models.BooleanField(default=True, verbose_name=_("Include in analytics"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("edition_year", "pk")
        verbose_name = _("Legacy edition")
        verbose_name_plural = _("Legacy editions")

    def __str__(self):
        return f"{self.label} ({self.edition_year})"


class LegacyIdentity(models.Model):
    legacy_edition = models.ForeignKey(
        LegacyEdition,
        on_delete=models.CASCADE,
        related_name="identities",
    )
    identity_type = models.CharField(max_length=32, default="email")
    identity_hash = models.CharField(max_length=64, db_index=True)
    # Filled in by the series identity resolver.
    person_key = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["legacy_edition", "identity_type", "identity_hash"],
                name="unique_legacy_identity",
            ),
        ]
