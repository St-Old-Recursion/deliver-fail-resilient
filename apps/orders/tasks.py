"""
Системные задачи приложения orders.
"""
from __future__ import annotations

import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(queue="system")
def cleanup_expired_sagas() -> dict:
    """
    Периодически находит заказы, превысившие дедлайн саги, и компенсирует их.
    Для заказов в статусе PAID дополнительно выполняется возврат платежа.

    Запускается Celery Beat каждые 5 минут.
    """
    from django.utils import timezone
    from .models import Order, SagaLog
    from apps.payments.models import Payment
    from apps.payments import gateway as payment_gateway

    expired = Order.objects.filter(
        status__in=[
            Order.Status.PENDING,
            Order.Status.PROCESSING,
            Order.Status.PAID,
        ],
        deadline_at__lt=timezone.now(),
    )

    cancelled = 0
    refunded = 0

    for order in expired:
        if order.status == Order.Status.PAID:
            payment = Payment.objects.filter(
                order_id=order.pk, status=Payment.Status.SUCCESS
            ).first()
            if payment:
                payment_gateway.refund(payment.external_payment_id)
                Payment.objects.filter(pk=payment.pk).update(
                    status=Payment.Status.REFUNDED
                )
                refunded += 1

        Order.objects.filter(pk=order.pk).update(status=Order.Status.CANCELLED)
        SagaLog.objects.create(
            saga_id=f"cleanup-{order.pk}",
            step="saga_timeout",
            status=SagaLog.StepStatus.COMPENSATED,
            payload={"order_id": order.pk},
            error="Сага просрочена — автоматическая компенсация по дедлайну",
        )
        cancelled += 1
        logger.warning("cleanup_expired_sagas: заказ %d отменён по дедлайну", order.pk)

    return {"cancelled": cancelled, "refunded": refunded}
