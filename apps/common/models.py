from django.db import models


class OutboxMessage(models.Model):
    """
    Паттерн Transactional Outbox.
    Задача пишется в эту таблицу в рамках той же транзакции что и бизнес-данные,
    а отдельный воркер flush_outbox перекладывает её в RabbitMQ.
    Это исключает рассогласование между БД и брокером при падении процесса.
    """
    class Status(models.TextChoices):
        PENDING = "PENDING"
        SENT = "SENT"

    task_name = models.CharField(max_length=255)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "outbox_messages"
        indexes = [models.Index(fields=["status", "created_at"])]


class FailedTask(models.Model):
    """
    Dead Letter Queue в виде таблицы БД.
    Задача попадает сюда после исчерпания всех попыток retry.
    Позволяет вручную разобрать потерянные задачи и при необходимости переиграть их.
    """
    task_name = models.CharField(max_length=255)
    task_id = models.CharField(max_length=255)
    payload = models.JSONField(default=dict)
    exception = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "failed_tasks"
