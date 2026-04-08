"""
Migration for performance and integrity improvements:
- Add db_index on is_repeat_buyer and payment_provider
- Add UniqueConstraint on AnalyticsTicketFact (order_fact, item_id, variation_id)
- Remove redundant index on AnalyticsIdentity (identity_hash, identity_type)
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pretix_event_analytics", "0003_audit_fixes"),
    ]

    operations = [
        # 1. Add db_index on is_repeat_buyer
        migrations.AlterField(
            model_name="analyticsorderfact",
            name="is_repeat_buyer",
            field=models.BooleanField(default=False, db_index=True),
        ),
        # 2. Add db_index on payment_provider
        migrations.AlterField(
            model_name="analyticsorderfact",
            name="payment_provider",
            field=models.CharField(max_length=100, blank=True, db_index=True),
        ),
        # 3. Add UniqueConstraint on AnalyticsTicketFact
        migrations.AddConstraint(
            model_name="analyticsticketfact",
            constraint=models.UniqueConstraint(
                fields=["order_fact", "item_id", "variation_id"],
                name="unique_ticket_per_order_item",
            ),
        ),
        # 4. Remove redundant index on AnalyticsIdentity
        migrations.RemoveIndex(
            model_name="analyticsidentity",
            name="pretix_even_identit_13cb2e_idx",
        ),
    ]
