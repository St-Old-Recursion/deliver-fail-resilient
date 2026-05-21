"""
Оркестрационная сага для оформления заказа.

В данной работе реализован паттерн Saga с оркестратором — центральным объектом,
который управляет последовательностью шагов и компенсирующими транзакциями при сбоях.

Поток выполнения:
  1. create_order    → Заказ переходит в статус PENDING
  2. process_payment → Платёж успешен, заказ переходит в PAID
  3. confirm_order   → Заказ переходит в COMPLETED, ставится уведомление в очередь

Компенсации при сбоях:
  - Сбой на шаге платежа  → Заказ отменяется (CANCELLED)
  - Сбой на шаге подтверждения → Платёж возвращается, заказ отменяется

Состояние саги сохраняется в таблице SagaLog, что позволяет восстановить
процесс после падения сервиса или перезапуска воркера.
"""
from __future__ import annotations

import uuid
import logging
from decimal import Decimal

from django.db import transaction

from .models import Order, SagaLog
from apps.payments.models import Payment
from apps.payments import gateway as payment_gateway
from apps.common.exceptions import TemporaryFailure, CircuitOpenError

logger = logging.getLogger(__name__)

Step = str


class OrderSagaOrchestrator:
    STEP_CREATE = "create_order"
    STEP_PAYMENT = "process_payment"
    STEP_CONFIRM = "confirm_order"

    def __init__(self, saga_id: str | None = None) -> None:
        self.saga_id: str = saga_id or uuid.uuid4().hex

    # ------------------------------------------------------------------ #
    # Точка входа
    # ------------------------------------------------------------------ #

    def execute(self, customer: str, restaurant: str, amount: Decimal) -> Order:
        """
        Запускает полный цикл саги синхронно.
        Платёж вызывается напрямую здесь; при асинхронном варианте — через Celery-задачу.
        """
        order = self._step_create_order(customer, restaurant, amount)
        try:
            external_id = self._step_process_payment(order)
        except (TemporaryFailure, CircuitOpenError) as exc:
            self._compensate_create(order, str(exc))
            raise

        try:
            self._step_confirm_order(order, external_id)
        except Exception as exc:
            self._compensate_payment(order, external_id, str(exc))
            raise

        return order

    # ------------------------------------------------------------------ #
    # Шаги саги
    # ------------------------------------------------------------------ #

    def _step_create_order(
        self, customer: str, restaurant: str, amount: Decimal
    ) -> Order:
        self._log(self.STEP_CREATE, SagaLog.StepStatus.STARTED)
        with transaction.atomic():
            order = Order.objects.create(
                customer=customer,
                restaurant=restaurant,
                amount=amount,
                status=Order.Status.PENDING,
            )
        self._log(self.STEP_CREATE, SagaLog.StepStatus.SUCCESS, {"order_id": order.pk})
        logger.info("Сага %s: заказ %d создан", self.saga_id, order.pk)
        return order

    def _step_process_payment(self, order: Order) -> str:
        """Списание через шлюз; вызов защищён автоматическим выключателем внутри gateway.charge."""
        self._log(self.STEP_PAYMENT, SagaLog.StepStatus.STARTED, {"order_id": order.pk})
        external_id = payment_gateway.charge(order.pk, float(order.amount))

        with transaction.atomic():
            Payment.objects.create(
                order_id=order.pk,
                amount=order.amount,
                status=Payment.Status.SUCCESS,
                external_payment_id=external_id,
            )
            Order.objects.filter(pk=order.pk).update(status=Order.Status.PAID)
            order.status = Order.Status.PAID

        self._log(
            self.STEP_PAYMENT,
            SagaLog.StepStatus.SUCCESS,
            {"order_id": order.pk, "external_id": external_id},
        )
        return external_id

    def run_payment_step(self, order_id: int, amount: float) -> None:
        """Вызывается из Celery-задачи после десериализации аргументов."""
        order = Order.objects.get(pk=order_id)
        external_id = self._step_process_payment(order)
        self._step_confirm_order(order, external_id)

    def _step_confirm_order(self, order: Order, external_id: str) -> None:
        self._log(self.STEP_CONFIRM, SagaLog.StepStatus.STARTED, {"order_id": order.pk})
        with transaction.atomic():
            Order.objects.filter(pk=order.pk).update(status=Order.Status.COMPLETED)
            order.status = Order.Status.COMPLETED

        self._log(self.STEP_CONFIRM, SagaLog.StepStatus.SUCCESS, {"order_id": order.pk})
        self._enqueue_notification(order)
        logger.info("Сага %s: заказ %d завершён", self.saga_id, order.pk)

    # ------------------------------------------------------------------ #
    # Компенсирующие транзакции
    # ------------------------------------------------------------------ #

    def _compensate_create(self, order: Order, error: str) -> None:
        self._log(
            self.STEP_CREATE, SagaLog.StepStatus.COMPENSATED,
            {"order_id": order.pk}, error=error,
        )
        Order.objects.filter(pk=order.pk).update(status=Order.Status.CANCELLED)
        logger.warning("Сага %s: заказ %d отменён (сбой платежа)", self.saga_id, order.pk)

    def _compensate_payment(self, order: Order, external_id: str, error: str) -> None:
        self._log(
            self.STEP_PAYMENT, SagaLog.StepStatus.COMPENSATED,
            {"order_id": order.pk, "external_id": external_id}, error=error,
        )
        payment_gateway.refund(external_id)
        Payment.objects.filter(order_id=order.pk).update(status=Payment.Status.REFUNDED)
        Order.objects.filter(pk=order.pk).update(status=Order.Status.CANCELLED)
        logger.warning("Сага %s: заказ %d возвращён (возврат средств)", self.saga_id, order.pk)

    # ------------------------------------------------------------------ #
    # Вспомогательные методы
    # ------------------------------------------------------------------ #

    def _log(
        self,
        step: Step,
        status: str,
        payload: dict | None = None,
        error: str = "",
    ) -> None:
        SagaLog.objects.create(
            saga_id=self.saga_id,
            step=step,
            status=status,
            payload=payload or {},
            error=error,
        )

    def _enqueue_notification(self, order: Order) -> None:
        from apps.notifications.models import Notification
        from apps.notifications.tasks import send_notification_task

        n = Notification.objects.create(
            recipient=order.customer,
            type=Notification.Type.EMAIL,
            message=f"Ваш заказ #{order.pk} подтверждён!",
        )
        send_notification_task.delay(
            notification_id=n.pk,
            recipient=n.recipient,
            notification_type=n.type,
            message=n.message,
        )
