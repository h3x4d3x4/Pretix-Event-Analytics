"""
Forms for event analytics configuration and series management.
"""
import pycountry
from django import forms
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from pretix.base.forms.widgets import DatePickerWidget

from .models import EventAnalyticsConfig, EventSeries
from ._compat import VIEW_ORDERS


def _country_choices():
    """Return (alpha_2, name) choices sorted by name, with blank first."""
    countries = sorted(pycountry.countries, key=lambda c: c.name)
    return [("", _("— Select country —"))] + [(c.alpha_2, c.name) for c in countries]


class EventSeriesForm(forms.ModelForm):
    """Create or edit an EventSeries at the organizer level."""

    class Meta:
        model = EventSeries
        fields = ["name", "slug"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": _("e.g. Suti Festival")}
            ),
            "slug": forms.TextInput(
                attrs={"class": "form-control", "placeholder": _("e.g. suti-festival")}
            ),
        }
        help_texts = {
            "slug": _(
                "Short unique identifier used internally. "
                "Cannot be changed after editions are linked to this series."
            ),
        }

    def __init__(self, *args, organizer=None, **kwargs):
        self.organizer = organizer
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # Prevent slug changes once editions exist
            if self.instance.event_configs.exists():
                self.fields["slug"].disabled = True
                self.fields["slug"].help_text = _(
                    "Slug cannot be changed — editions are already linked to this series."
                )

    def clean_slug(self):
        raw = self.cleaned_data.get("slug", "").strip()
        if not raw:
            raise forms.ValidationError(_("Slug is required."))
        # Auto-slugify to ensure URL-safety
        slug = slugify(raw)
        if not slug:
            raise forms.ValidationError(_("Slug must contain at least one alphanumeric character."))
        qs = EventSeries.objects.filter(organizer=self.organizer, slug=slug)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(
                _("A series with this slug already exists for this organiser.")
            )
        return slug

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.organizer = self.organizer
        if commit:
            instance.save()
        return instance


class EventAnalyticsConfigForm(forms.ModelForm):
    """Configure analytics settings for a specific event."""

    class Meta:
        model = EventAnalyticsConfig
        fields = ["series", "edition_year", "home_country", "is_active", "ticket_target", "revenue_target"]
        widgets = {
            "series": forms.Select(attrs={"class": "form-control"}),
            "edition_year": forms.NumberInput(
                attrs={"class": "form-control pa-input-sm", "min": 1990, "max": 2100}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "ticket_target": forms.NumberInput(attrs={"class": "form-control pa-input-md"}),
            "revenue_target": forms.NumberInput(attrs={"class": "form-control pa-input-md"}),
        }

    def __init__(self, *args, event=None, **kwargs):
        self.event = event
        super().__init__(*args, **kwargs)
        if event:
            self.fields["series"].queryset = EventSeries.objects.filter(
                organizer=event.organizer
            ).order_by("name")
        self.fields["series"].empty_label = _("— Select a series (optional) —")
        self.fields["series"].required = False
        # Replace home_country with a proper country Select
        self.fields["home_country"] = forms.ChoiceField(
            choices=_country_choices(),
            required=False,
            label=_("Event Country"),
            widget=forms.Select(attrs={"class": "form-control pa-input-lg"}),
        )
        # Pre-select current value if editing
        if self.instance and self.instance.home_country:
            self.fields["home_country"].initial = self.instance.home_country

        # Opt-in answer analytics: only fixed-choice question types, so no
        # free text can ever reach the analytics tables.
        question_choices = []
        if event:
            from django_scopes import scopes_disabled
            with scopes_disabled():
                for q in event.questions.filter(type__in=("B", "C", "M")).order_by("position", "pk"):
                    question_choices.append((str(q.pk), str(q.question)))
        self.fields["tracked_questions"] = forms.MultipleChoiceField(
            choices=question_choices,
            required=False,
            widget=forms.CheckboxSelectMultiple,
            label=_("Questions to analyse"),
            help_text=_(
                "Answers to these yes/no and multiple-choice questions are counted on the "
                "Operations page. Only the chosen option is stored — never free text. "
                "Avoid questions about health or other sensitive topics unless you have a legal basis. "
                "Changes apply after the next resync."
            ),
        )
        if self.instance and self.instance.tracked_question_ids:
            self.fields["tracked_questions"].initial = [str(i) for i in self.instance.tracked_question_ids]

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.tracked_question_ids = [int(i) for i in self.cleaned_data.get("tracked_questions") or []]
        if commit:
            instance.save()
        return instance

    def clean_home_country(self):
        return self.cleaned_data.get("home_country") or ""

    def clean_edition_year(self):
        year = self.cleaned_data.get("edition_year")
        if year is not None and (year < 1990 or year > 2100):
            raise forms.ValidationError(_("Edition year must be between 1990 and 2100."))
        return year


AGE_BUCKETS = ["0-17", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"]

PROVIDER_LABELS = {
    "banktransfer": _("Bank transfer"),
    "stripe": _("Card (Stripe)"),
    "stripe_cc": _("Card (Stripe)"),
    "paypal": "PayPal",
    "paypal2": "PayPal",
    "free": _("Free"),
    "manual": _("Manual"),
    "boxoffice": _("Box office"),
    "giftcard": _("Gift card"),
    "cash": _("Cash"),
}


def provider_label(identifier: str) -> str:
    return str(PROVIDER_LABELS.get(identifier, identifier.replace("_", " ").title()))



class DashboardFilterForm(forms.Form):
    """
    Shared filter bar for every analytics page. All fields optional —
    blank means "show all". Applied by ``services.reports.scope``.

    Choices are populated from stored facts of the event's series (or the
    event alone), so products/countries of every comparable edition appear.
    """
    date_from = forms.DateField(
        required=False,
        widget=DatePickerWidget(attrs={"class": "form-control input-sm"}),
        label=_("Ordered from"),
    )
    date_to = forms.DateField(
        required=False,
        widget=DatePickerWidget(attrs={"class": "form-control input-sm"}),
        label=_("Ordered until"),
    )
    country = forms.ChoiceField(required=False, label=_("Country"),
                                widget=forms.Select(attrs={"class": "form-control input-sm"}))
    age_range = forms.ChoiceField(
        required=False,
        choices=[("", _("All ages"))] + [(a, a.replace("-", "–")) for a in AGE_BUCKETS],
        widget=forms.Select(attrs={"class": "form-control input-sm"}),
        label=_("Age"),
    )
    buyer_type = forms.ChoiceField(
        required=False,
        choices=[("", _("New and returning")), ("new", _("First-time buyers")), ("returning", _("Returning buyers"))],
        widget=forms.Select(attrs={"class": "form-control input-sm"}),
        label=_("Buyers"),
    )
    provider = forms.ChoiceField(required=False, label=_("Payment method"),
                                 widget=forms.Select(attrs={"class": "form-control input-sm"}))
    ticket_type = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "ticket-type-checkbox"}),
        label=_("Products"),
    )
    editions = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "edition-checkbox"}),
        label=_("Events"),
        help_text=_("Select several to merge their data."),
    )
    include_refunded = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label=_("Include canceled & refunded"),
    )
    has_caravan = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label=_("Caravan pass only"),
    )
    # Pre-2.0 URLs used ?repeat_only=on — still honoured.
    repeat_only = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, event=None, request=None, **kwargs):
        self._event = event
        self._request = request
        super().__init__(*args, **kwargs)
        self.fields["country"].choices = [("", _("All countries"))]
        self.fields["provider"].choices = [("", _("All methods"))]
        self.has_caravan_data = False
        if not event:
            return

        from .models import AnalyticsOrderFact, AnalyticsTicketFact, EventAnalyticsConfig

        config = EventAnalyticsConfig.objects.select_related("series").filter(event=event).first()
        if config and config.series:
            peer_ids = list(EventAnalyticsConfig.objects.filter(series=config.series).values_list("event_id", flat=True))
        else:
            peer_ids = [event.pk]

        names = (
            AnalyticsTicketFact.objects.filter(event_id__in=peer_ids)
            .values_list("item_name", flat=True).distinct().order_by("item_name")
        )
        self.fields["ticket_type"].choices = [(n, n) for n in names]

        facts = AnalyticsOrderFact.objects.filter(event_id__in=peer_ids)
        countries = facts.exclude(country_code="").values_list("country_code", flat=True).distinct()
        country_choices = []
        for c in countries:
            try:
                country_choices.append((c, pycountry.countries.get(alpha_2=c).name))
            except Exception:
                country_choices.append((c, c))
        self.fields["country"].choices += sorted(country_choices, key=lambda x: str(x[1]))

        providers = facts.exclude(payment_provider="").values_list("payment_provider", flat=True).distinct()
        self.fields["provider"].choices += [(p, provider_label(p)) for p in sorted(providers)]
        self.has_caravan_data = facts.filter(has_caravan_pass=True).exists()

        all_events = self._permitted_events().select_related("analytics_config").order_by(
            "-analytics_config__edition_year", "-date_from")
        self.fields["editions"].choices = [
            (str(e.id), f"{e.name} ({e.analytics_config.edition_year})") for e in all_events
        ]

    def clean_editions(self):
        """
        Defence in depth: even if a client posts arbitrary event ids, only
        ids that belong to this event's organizer and have an analytics
        config are accepted. Anything else is silently dropped.
        """
        submitted = self.cleaned_data.get("editions") or []
        if not submitted or not self._event:
            return submitted
        try:
            submitted_ids = [int(e) for e in submitted]
        except (TypeError, ValueError):
            return []
        valid_ids = set(self._permitted_events().filter(id__in=submitted_ids).values_list("id", flat=True))
        return [str(i) for i in submitted_ids if i in valid_ids]

    def _permitted_events(self):
        """
        Configured events of this organizer that the current user may see
        orders of. Team permissions can be limited to single events, so
        merging editions must never widen what a user can read.
        """
        from pretix.base.models import Event

        qs = Event.objects.filter(organizer=self._event.organizer, analytics_config__isnull=False)
        request = self._request
        if request is None or not getattr(request, "user", None):
            return qs.filter(pk=self._event.pk)
        allowed = request.user.get_events_with_permission(VIEW_ORDERS, request)
        return qs.filter(pk__in=allowed.values("pk"))

    def clean(self):
        data = super().clean()
        if data.get("date_from") and data.get("date_to") and data["date_from"] > data["date_to"]:
            raise forms.ValidationError(_("‘From’ date must be on or before ‘To’ date."))
        if data.get("repeat_only") and not data.get("buyer_type"):
            data["buyer_type"] = "returning"
        return data


class LegacyImportForm(forms.Form):
    """Upload a past attendee list; only HMAC hashes of the e-mails are kept."""
    label = forms.CharField(max_length=100, label=_("Edition name"),
                            widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Suti 2019"}))
    edition_year = forms.IntegerField(min_value=1990, max_value=2100, label=_("Edition year"),
                                      widget=forms.NumberInput(attrs={"class": "form-control pa-input-sm"}))
    emails_file = forms.FileField(
        required=False, label=_("CSV or text file"),
        help_text=_("Any file containing e-mail addresses — one per line or a CSV column. Other data is ignored."),
    )
    emails_text = forms.CharField(
        required=False, label=_("…or paste addresses"),
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 5, "placeholder": "one@example.org\ntwo@example.org"}),
    )

    MAX_BYTES = 20 * 1024 * 1024

    def clean(self):
        data = super().clean()
        f = data.get("emails_file")
        if f and f.size > self.MAX_BYTES:
            raise forms.ValidationError(_("The file is too large (20 MB maximum)."))
        if not f and not (data.get("emails_text") or "").strip():
            raise forms.ValidationError(_("Upload a file or paste some addresses."))
        return data
