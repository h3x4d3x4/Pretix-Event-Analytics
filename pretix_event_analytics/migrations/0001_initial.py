import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("pretixbase", "0001_initial"),
    ]

    operations = [
        # ── EventSeries ──────────────────────────────────────────────────────
        migrations.CreateModel(
            name="EventSeries",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                (
                    "organizer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="analytics_series",
                        to="pretixbase.organizer",
                    ),
                ),
                ("name", models.CharField(max_length=200, verbose_name="Series name")),
                ("slug", models.SlugField(max_length=200, verbose_name="Slug")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Event Series",
                "verbose_name_plural": "Event Series",
            },
        ),
        migrations.AddConstraint(
            model_name="eventseries",
            constraint=models.UniqueConstraint(
                fields=["organizer", "slug"], name="unique_organizer_series_slug"
            ),
        ),
        migrations.AddIndex(
            model_name="eventseries",
            index=models.Index(fields=["organizer", "slug"], name="analytics_series_org_slug_idx"),
        ),

        # ── EventAnalyticsConfig ─────────────────────────────────────────────
        migrations.CreateModel(
            name="EventAnalyticsConfig",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                (
                    "event",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="analytics_config",
                        to="pretixbase.event",
                    ),
                ),
                (
                    "series",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="event_configs",
                        to="pretix_event_analytics.eventseries",
                        verbose_name="Series",
                    ),
                ),
                ("edition_year", models.IntegerField(db_index=True, verbose_name="Edition year")),
                (
                    "home_country",
                    models.CharField(
                        blank=True,
                        max_length=2,
                        verbose_name="Home country (ISO alpha-2)",
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Include in analytics")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Event Analytics Config",
                "verbose_name_plural": "Event Analytics Configs",
            },
        ),

        # ── AnalyticsOrderFact ───────────────────────────────────────────────
        migrations.CreateModel(
            name="AnalyticsOrderFact",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ("organizer_id", models.IntegerField(db_index=True)),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="analytics_order_facts",
                        to="pretixbase.event",
                    ),
                ),
                ("series_slug", models.CharField(blank=True, db_index=True, max_length=200)),
                ("edition_year", models.IntegerField(blank=True, db_index=True, null=True)),
                ("order_code", models.CharField(db_index=True, max_length=50)),
                ("order_datetime", models.DateTimeField(db_index=True)),
                ("payment_datetime", models.DateTimeField(blank=True, null=True)),
                ("order_status", models.CharField(max_length=1)),
                ("total_gross", models.DecimalField(decimal_places=2, default=0, max_digits=13)),
                ("total_net", models.DecimalField(decimal_places=2, default=0, max_digits=13)),
                ("tax_amount", models.DecimalField(decimal_places=2, default=0, max_digits=13)),
                ("currency", models.CharField(blank=True, max_length=10)),
                ("ticket_count", models.IntegerField(default=0)),
                ("unique_attendee_count", models.IntegerField(default=0)),
                ("is_group_order", models.BooleanField(default=False)),
                ("payment_provider", models.CharField(blank=True, max_length=100)),
                ("is_refunded", models.BooleanField(default=False)),
                ("country_code", models.CharField(blank=True, db_index=True, max_length=2)),
                ("city", models.CharField(blank=True, max_length=100)),
                ("postal_code", models.CharField(blank=True, max_length=20)),
                ("age_range", models.CharField(blank=True, max_length=10)),
                ("is_age_confirmed", models.BooleanField(blank=True, null=True)),
                ("language", models.CharField(blank=True, max_length=10)),
                ("has_caravan_pass", models.BooleanField(default=False)),
                ("camper_van_length_bucket", models.CharField(blank=True, max_length=10)),
                ("repeat_hash", models.CharField(blank=True, db_index=True, max_length=64)),
                ("is_repeat_buyer", models.BooleanField(default=False)),
                ("repeat_from_last_edition", models.BooleanField(default=False)),
                ("repeat_from_any_previous", models.BooleanField(default=False)),
                ("repeat_count", models.IntegerField(default=0)),
                ("first_seen_edition_year", models.IntegerField(blank=True, null=True)),
                ("checkin_completed", models.BooleanField(default=False)),
                ("predicted_repeat_probability", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Analytics Order Fact",
                "verbose_name_plural": "Analytics Order Facts",
            },
        ),
        migrations.AddIndex(
            model_name="analyticsorderfact",
            index=models.Index(fields=["event"], name="aof_event_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticsorderfact",
            index=models.Index(fields=["repeat_hash"], name="aof_repeat_hash_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticsorderfact",
            index=models.Index(fields=["country_code"], name="aof_country_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticsorderfact",
            index=models.Index(fields=["order_datetime"], name="aof_order_dt_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticsorderfact",
            index=models.Index(fields=["event", "repeat_hash"], name="aof_event_hash_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticsorderfact",
            index=models.Index(fields=["event", "edition_year"], name="aof_event_year_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticsorderfact",
            index=models.Index(fields=["event", "country_code"], name="aof_event_country_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticsorderfact",
            index=models.Index(
                fields=["series_slug", "edition_year"], name="aof_series_year_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="analyticsorderfact",
            index=models.Index(
                fields=["organizer_id", "series_slug", "edition_year"],
                name="aof_org_series_year_idx",
            ),
        ),

        # ── AnalyticsTicketFact ──────────────────────────────────────────────
        migrations.CreateModel(
            name="AnalyticsTicketFact",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                (
                    "order_fact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ticket_facts",
                        to="pretix_event_analytics.analyticsorderfact",
                    ),
                ),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="analytics_ticket_facts",
                        to="pretixbase.event",
                    ),
                ),
                ("item_id", models.IntegerField(db_index=True)),
                ("item_name", models.CharField(max_length=255)),
                ("variation_id", models.IntegerField(blank=True, null=True)),
                ("variation_name", models.CharField(blank=True, max_length=255)),
                ("price", models.DecimalField(decimal_places=2, default=0, max_digits=13)),
                ("tax_rate", models.DecimalField(decimal_places=4, default=0, max_digits=7)),
                ("net_price", models.DecimalField(decimal_places=2, default=0, max_digits=13)),
                ("is_addon", models.BooleanField(default=False)),
                ("age_range", models.CharField(blank=True, max_length=10)),
                ("is_age_confirmed", models.BooleanField(blank=True, null=True)),
                ("is_caravan_pass", models.BooleanField(default=False)),
                ("camper_van_length_bucket", models.CharField(blank=True, max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Analytics Ticket Fact",
                "verbose_name_plural": "Analytics Ticket Facts",
            },
        ),
        migrations.AddIndex(
            model_name="analyticsticketfact",
            index=models.Index(fields=["event", "item_id"], name="atf_event_item_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticsticketfact",
            index=models.Index(fields=["order_fact"], name="atf_order_fact_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticsticketfact",
            index=models.Index(fields=["event", "is_addon"], name="atf_event_addon_idx"),
        ),
    ]
