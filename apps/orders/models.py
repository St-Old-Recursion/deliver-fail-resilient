from django.db import models


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        PROCESSING = "PROCESSING"   # сага захватила заказ, идёт списание
        PAID = "PAID"
        PREPARING = "PREPARING"
        DELIVERING = "DELIVERING"
        COMPLETED = "COMPLETED"
        CANCELLED = "CANCELLED"

    customer = models.CharField(max_length=255)
    restaurant = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # Дедлайн саги: если заказ не завершён до этого времени — автоматическая компенсация
    deadline_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "orders"


class SagaLog(models.Model):
    class StepStatus(models.TextChoices):
        STARTED = "STARTED"
        SUCCESS = "SUCCESS"
        FAILED = "FAILED"
        COMPENSATED = "COMPENSATED"

    saga_id = models.CharField(max_length=64, db_index=True)
    step = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=StepStatus.choices)
    payload = models.JSONField(default=dict)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "saga_logs"
        ordering = ["created_at"]
