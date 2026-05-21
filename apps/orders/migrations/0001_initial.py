from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Order",
            fields=[
                ("id", models.BigAutoField(primary_key=True)),
                ("customer", models.CharField(max_length=255)),
                ("restaurant", models.CharField(max_length=255)),
                ("amount", models.DecimalField(max_digits=10, decimal_places=2)),
                ("status", models.CharField(max_length=20, default="PENDING")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "orders"},
        ),
        migrations.CreateModel(
            name="SagaLog",
            fields=[
                ("id", models.BigAutoField(primary_key=True)),
                ("saga_id", models.CharField(max_length=64, db_index=True)),
                ("step", models.CharField(max_length=64)),
                ("status", models.CharField(max_length=20)),
                ("payload", models.JSONField(default=dict)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "saga_logs", "ordering": ["created_at"]},
        ),
    ]
