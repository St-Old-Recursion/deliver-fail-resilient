"""
Оркестрационная сага для оформления заказа.

В данной работе реализован паттерн Saga с оркестратором — центральным объектом,
который управляет последовательностью шагов и компенсирующими транзакциями при сбоях.

Поток выполнения:
  1. create_order    → Заказ переходит в статус PENDING
  2. process_payment → Атомарный захват PENDING→PROCESSING, списание, заказ PAID
  3. confirm_order   → Заказ COMPLETED, уведомление через Outbox

Компенсации при сбоях:
  - Сбой на шаге платежа       → Заказ отменяется (CANCELLED)
  - Сбой на шаге подтверждения → Платёж возвращается, заказ отменяется

Исправленные проблемы надёжности:
  - Идемпотентность: повторный вызов не создаёт второй платёж
  - Race condition: атомарный захват статуса исключает гонку с cancel
  - Дедлайн: сага имеет временной лимит, по истечении — компенсация
  - Transactional Outbox: уведомление пишется в БД в той же транзакции
"""
from __future__ import annotations

import uuid
import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import Order, SagaLog
from apps.payments.models import Payment
from apps.payments import gateway as payment_gateway
from apps.common.exceptions import (
    TemporaryFailure,
    CircuitOpenError,
    SagaExpiredError,
    OrderStateError,
)

logger = logging.getLogger(__name__)

Step = str
SAGA_TTL_MINUTES = 30


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
        """Запускает полный цикл саги синхронно."""
        order = self._step_create_order(customer, restaurant, amount)
        try:
            external_id = self._step_process_payment(order)
        except (TemporaryFailure, CircuitOpenError, SagaExpiredError, OrderStateError) as exc:
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
        deadline = timezone.now() + timedelta(minutes=SAGA_TTL_MINUTES)
        with transaction.atomic():
            order = Order.objects.create(
                customer=customer,
                restaurant=restaurant,
                amount=amount,
                status=Order.Status.PENDING,
                deadline_at=deadline,
            )
        self._log(self.STEP_CREATE, SagaLog.StepStatus.SUCCESS, {"order_id": order.pk})
        logger.info("Сага %s: заказ %d создан, дедлайн %s", self.saga_id, order.pk, deadline)
        return order

    def _step_process_payment(self, order: Order) -> str:
        """
        Шаг списания. Содержит три механизма защиты:
        1. Идемпотентность — повторный вызов при retry не создаёт второй платёж.
        2. Race fix — атомарный захват статуса PENDING→PROCESSING блокирует cancel.
        3. Deadline — если сага просрочена, компенсируем не обращаясь к шлюзу.
        """
        # 1. Идемпотентность: платёж уже проведён (retry после частичного успеха)
        existing = Payment.objects.filter(
            order_id=order.pk, status=Payment.Status.SUCCESS
        ).first()
        if existing:
            logger.info("Сага %s: платёж уже существует, пропускаем списание", self.saga_id)
            return existing.external_payment_id

        # 2. Проверка дедлайна
        fresh = Order.objects.get(pk=order.pk)
        if fresh.deadline_at and timezone.now() > fresh.deadline_at:
            raise SagaExpiredError(f"Сага для заказа {order.pk} просрочена")

        # 3. Атомарный захват: PENDING или PROCESSING → PROCESSING
        #    PENDING→PROCESSING: первый запуск или retry до начала списания.
        #    Если статус уже CANCELLED (пользователь успел отменить) — upd = 0, бросаем ошибку.
        captured = Order.objects.filter(
            pk=order.pk,
            status__in=[Order.Status.PENDING, Order.Status.PROCESSING],
        ).update(status=Order.Status.PROCESSING)

        if not captured:
            current = Order.objects.values_list("status", flat=True).get(pk=order.pk)
            raise OrderStateError(
                f"Заказ {order.pk} в статусе '{current}' — оплата невозможна"
            )
        order.status = Order.Status.PROCESSING

        self._log(self.STEP_PAYMENT, SagaLog.StepStatus.STARTED, {"order_id": order.pk})

        external_id = payment_gateway.charge(
            order.pk, float(order.amount), idempotency_key=self.saga_id
        )

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
            # Уведомление пишется в outbox в рамках той же транзакции (Transactional Outbox)
            self._write_notification_to_outbox(order)

        self._log(self.STEP_CONFIRM, SagaLog.StepStatus.SUCCESS, {"order_id": order.pk})
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

    def _write_notification_to_outbox(self, order: Order) -> None:
        """
        Transactional Outbox: вместо прямого вызова .delay() пишем запись в БД.
        Отдельный воркер flush_outbox прочитает её и поставит задачу в RabbitMQ.
        Это гарантирует атомарность: либо заказ COMPLETED И уведомление в outbox,
        либо ни то ни другое.
        """
        from apps.notifications.models import Notification
        from apps.common.models import OutboxMessage

        n = Notification.objects.create(
            recipient=order.customer,
            type=Notification.Type.EMAIL,
            message=f"Ваш заказ #{order.pk} подтверждён!",
        )
        OutboxMessage.objects.create(
            task_name="apps.notifications.tasks.send_notification_task",
            payload={
                "notification_id": n.pk,
                "recipient": n.recipient,
                "notification_type": n.type,
                "message": n.message,
            },
        )
