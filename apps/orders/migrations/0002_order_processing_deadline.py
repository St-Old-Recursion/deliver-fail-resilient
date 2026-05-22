from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="deadline_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        # PROCESSING — промежуточный статус; choices в Django не создают ограничение на уровне БД,
        # поэтому отдельной операции AlterField не требуется.
    ]
