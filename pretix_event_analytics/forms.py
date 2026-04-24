"""
Forms for event analytics configuration and series management.
"""
import pycountry
from django import forms
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from pretix.base.forms.widgets import DatePickerWidget

from .models import EventAnalyticsConfig, EventSeries


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
        fields = ["series", "edition_year", "home_country", "is_active"]
        widgets = {
            "series": forms.Select(attrs={"class": "form-control"}),
            "edition_year": forms.NumberInput(
                attrs={"class": "form-control", "min": 1990, "max": 2100, "style": "width:110px;"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
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
            widget=forms.Select(attrs={"class": "form-control", "style": "max-width:320px;"}),
        )
        # Pre-select current value if editing
        if self.instance and self.instance.home_country:
            self.fields["home_country"].initial = self.instance.home_country

    def clean_home_country(self):
        return self.cleaned_data.get("home_country") or ""

    def clean_edition_year(self):
        year = self.cleaned_data.get("edition_year")
        if year is not None and (year < 1990 or year > 2100):
            raise forms.ValidationError(_("Edition year must be between 1990 and 2100."))
        return year


class DashboardFilterForm(forms.Form):
    """
    Dashboard filter bar.  All fields optional — blank means "show all".
    Applied via AnalyticsOrderFact queryset filtering in the view.

    Pass event= to populate the ticket_type dropdown from stored fact data.
    """
    date_from = forms.DateField(
        required=False,
        widget=DatePickerWidget(attrs={"class": "form-control form-control-sm"}),
        label=_("From"),
    )
    date_to = forms.DateField(
        required=False,
        widget=DatePickerWidget(attrs={"class": "form-control form-control-sm"}),
        label=_("To"),
    )
    country = forms.ChoiceField(
        required=False,
        choices=[("", _("All countries"))],
        widget=forms.Select(attrs={"class": "form-control form-control-sm"}),
        label=_("Country"),
    )
    age_range = forms.ChoiceField(
        required=False,
        choices=[
            ("", _("All ages")),
            ("0-17", "0–17"),
            ("18-24", "18–24"),
            ("25-34", "25–34"),
            ("35-44", "35–44"),
            ("45-54", "45–54"),
            ("55-64", "55–64"),
            ("65+", "65+"),
        ],
        widget=forms.Select(attrs={"class": "form-control form-control-sm"}),
        label=_("Age range"),
    )
    repeat_only = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label=_("Returning buyers only"),
    )
    include_refunded = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label=_("Include refunded"),
    )
    has_caravan = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label=_("Caravan pass only"),
    )
    ticket_type = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple(
            attrs={"class": "ticket-type-checkbox"}
        ),
        label=_("Ticket types"),
    )
    editions = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple(
            attrs={"class": "edition-checkbox"}
        ),
        label=_("Events"),
        help_text=_("Select multiple to merge event stats."),
    )

    def __init__(self, *args, event=None, **kwargs):
        # Retained for clean() — enforces organizer scope for any submitted
        # edition ids regardless of client-side choices tampering.
        self._event = event
        super().__init__(*args, **kwargs)
        if event:
            from .models import AnalyticsTicketFact
            names = (
                AnalyticsTicketFact.objects.filter(event=event, is_addon=False)
                .values_list("item_name", flat=True)
                .distinct()
                .order_by("item_name")
            )
            self.fields["ticket_type"].choices = [
                (name, name) for name in names
            ]

            from .models import AnalyticsOrderFact
            countries = (
                AnalyticsOrderFact.objects.filter(event=event)
                .exclude(country_code="")
                .values_list("country_code", flat=True)
                .distinct()
                .order_by("country_code")
            )
            import pycountry
            country_choices = []
            for c in countries:
                try:
                    country_name = pycountry.countries.get(alpha_2=c).name
                except Exception:
                    country_name = c
                country_choices.append((c, country_name))
            
            self.fields["country"].choices = [("", _("All countries"))] + country_choices

            # Populate editions with all events under the same organizer
            # that have an analytics config (meaning they are tracked editions).
            from pretix.base.models import Event
            all_events = Event.objects.filter(
                organizer=event.organizer,
                analytics_config__isnull=False
            ).select_related("analytics_config").order_by("-analytics_config__edition_year")
            
            self.fields["editions"].choices = [
                (str(e.id), f"{e.name} ({e.analytics_config.edition_year})")
                for e in all_events
            ]

    def clean_editions(self):
        """
        Defence in depth: even if a client posts arbitrary event ids,
        only ids that belong to this event's organizer and have an
        analytics config are accepted. Anything else is silently dropped.
        """
        submitted = self.cleaned_data.get("editions") or []
        if not submitted or not self._event:
            return submitted
        from pretix.base.models import Event
        try:
            submitted_ids = [int(e) for e in submitted]
        except (TypeError, ValueError):
            return []
        valid_ids = set(
            Event.objects.filter(
                organizer=self._event.organizer,
                id__in=submitted_ids,
                analytics_config__isnull=False,
            ).values_list("id", flat=True)
        )
        return [str(i) for i in submitted_ids if i in valid_ids]

    def clean(self):
        data = super().clean()
        if data.get("date_from") and data.get("date_to"):
            if data["date_from"] > data["date_to"]:
                raise forms.ValidationError(
                    _("‘From’ date must be on or before ‘To’ date.")
                )
        return data
