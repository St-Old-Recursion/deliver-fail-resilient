from django.db import models


class Notification(models.Model):
    class Type(models.TextChoices):
        EMAIL = "email"
        SMS = "sms"

    class Status(models.TextChoices):
        PENDING = "PENDING"
        SENT = "SENT"
        FAILED = "FAILED"

    recipient = models.CharField(max_length=255)
    type = models.CharField(max_length=10, choices=Type.choices)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    retry_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "notifications"
