from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Notification",
            fields=[
                ("id", models.BigAutoField(primary_key=True)),
                ("recipient", models.CharField(max_length=255)),
                ("type", models.CharField(max_length=10)),
                ("message", models.TextField()),
                ("status", models.CharField(max_length=20, default="PENDING")),
                ("retry_count", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "notifications"},
        ),
    ]
