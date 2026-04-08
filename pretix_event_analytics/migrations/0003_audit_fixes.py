"""
Migration for audit fixes:
- Add UniqueConstraint on (event, order_code) for AnalyticsOrderFact
- Add composite index on (event, order_status)
- Add ORDER_STATUS_CHOICES to order_status field
- Convert AnalyticsIdentity from unique_together to UniqueConstraint
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pretix_event_analytics", "0002_analyticsidentity_and_more"),
    ]

    operations = [
        # 1. Add UniqueConstraint on (event, order_code)
        migrations.AddConstraint(
            model_name="analyticsorderfact",
            constraint=models.UniqueConstraint(
                fields=["event", "order_code"],
                name="unique_event_order_code",
            ),
        ),
        # 2. Add composite index on (event, order_status)
        migrations.AddIndex(
            model_name="analyticsorderfact",
            index=models.Index(
                fields=["event", "order_status"],
                name="pretix_even_event_i_status_idx",
            ),
        ),
        # 3. Add choices to order_status field
        migrations.AlterField(
            model_name="analyticsorderfact",
            name="order_status",
            field=models.CharField(
                max_length=1,
                choices=[
                    ("n", "Pending"),
                    ("p", "Paid"),
                    ("e", "Expired"),
                    ("c", "Canceled"),
                ],
            ),
        ),
        # 4. Convert AnalyticsIdentity from unique_together to UniqueConstraint
        migrations.AlterUniqueTogether(
            name="analyticsidentity",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="analyticsidentity",
            constraint=models.UniqueConstraint(
                fields=["order_fact", "identity_type", "identity_hash"],
                name="unique_identity_per_order",
            ),
        ),
    ]
