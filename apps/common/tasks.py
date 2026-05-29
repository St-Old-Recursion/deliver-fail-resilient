"""
Системные Celery-задачи, обслуживающие инфраструктурные паттерны.
Запускаются через Celery Beat по расписанию.
"""
from __future__ import annotations

import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(queue="system", bind=True, max_retries=3, default_retry_delay=5)
def flush_outbox(self) -> dict:
    """
    Transactional Outbox: читает необработанные сообщения из таблицы outbox_messages
    и отправляет соответствующие Celery-задачи в RabbitMQ.
    """
    from celery import current_app
    from .models import OutboxMessage
    from django.db import transaction

    sent = 0
    with transaction.atomic():
        msgs = (
            OutboxMessage.objects
            .select_for_update(skip_locked=True)
            .filter(status=OutboxMessage.Status.PENDING)
            .order_by("created_at")[:50]
        )
        for msg in msgs:
            current_app.send_task(msg.task_name, kwargs=msg.payload)
            msg.status = OutboxMessage.Status.SENT
            msg.save(update_fields=["status"])
            sent += 1

    if sent:
        logger.info("flush_outbox: отправлено %d сообщений", sent)
    return {"sent": sent}
