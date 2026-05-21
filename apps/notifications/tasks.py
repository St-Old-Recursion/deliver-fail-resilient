from __future__ import annotations

import logging
from celery import shared_task
from apps.common.exceptions import TemporaryFailure

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    queue="notifications",
    max_retries=5,
    autoretry_for=(TemporaryFailure,),
    retry_backoff=True,
    retry_backoff_max=160,
    retry_jitter=True,
    acks_late=True,
)
def send_notification_task(
    self,
    notification_id: int,
    recipient: str,
    notification_type: str,
    message: str,
) -> dict:
    """
    Celery-задача отправки уведомления через очередь notifications.
    При временном сбое выполняет повторные попытки с экспоненциальной задержкой.
    Счётчик retry_count обновляется в БД при каждой неудачной попытке.
    """
    from .models import Notification
    from .service import send_email, send_sms

    logger.info(
        "send_notification_task попытка=%d id=%d тип=%s",
        self.request.retries,
        notification_id,
        notification_type,
    )

    try:
        if notification_type == Notification.Type.EMAIL:
            send_email(recipient, message)
        else:
            send_sms(recipient, message)
    except TemporaryFailure:
        # Фиксируем количество выполненных попыток перед очередным повтором
        Notification.objects.filter(pk=notification_id).update(
            retry_count=self.request.retries + 1
        )
        raise

    Notification.objects.filter(pk=notification_id).update(
        status=Notification.Status.SENT
    )
    return {"notification_id": notification_id, "sent": True}
