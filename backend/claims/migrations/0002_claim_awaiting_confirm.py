from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("claims", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="claim",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("in_progress", "In Progress"),
                    ("escalated", "Escalated"),
                    ("awaiting_confirm", "Awaiting confirmation"),
                    ("done", "Done"),
                ],
                default="pending",
                max_length=32,
            ),
        ),
    ]
