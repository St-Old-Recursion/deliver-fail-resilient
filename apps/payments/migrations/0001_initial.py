from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Payment",
            fields=[
                ("id", models.BigAutoField(primary_key=True)),
                ("order_id", models.BigIntegerField(db_index=True)),
                ("amount", models.DecimalField(max_digits=10, decimal_places=2)),
                ("status", models.CharField(max_length=20, default="PENDING")),
                ("external_payment_id", models.CharField(max_length=100, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "payments"},
        ),
    ]
