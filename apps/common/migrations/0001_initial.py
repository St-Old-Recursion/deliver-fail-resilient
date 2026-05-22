from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="OutboxMessage",
            fields=[
                ("id", models.BigAutoField(primary_key=True)),
                ("task_name", models.CharField(max_length=255)),
                ("payload", models.JSONField(default=dict)),
                ("status", models.CharField(max_length=20, default="PENDING")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "outbox_messages"},
        ),
        migrations.AddIndex(
            model_name="outboxmessage",
            index=models.Index(fields=["status", "created_at"], name="outbox_status_idx"),
        ),
        migrations.CreateModel(
            name="FailedTask",
            fields=[
                ("id", models.BigAutoField(primary_key=True)),
                ("task_name", models.CharField(max_length=255)),
                ("task_id", models.CharField(max_length=255)),
                ("payload", models.JSONField(default=dict)),
                ("exception", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "failed_tasks"},
        ),
    ]
