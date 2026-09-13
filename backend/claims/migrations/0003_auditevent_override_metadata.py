from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("claims", "0002_claim_awaiting_confirm"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditevent",
            name="original_outcome",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="auditevent",
            name="original_amount",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="auditevent",
            name="override_reason",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="auditevent",
            name="override_proposed_amount",
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
