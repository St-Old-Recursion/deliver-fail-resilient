"""
Интеграционные тесты оркестратора OrderSaga.

Запуск: pytest (требуется работающий PostgreSQL; в CI использовать docker-compose).
Покрывает:
  - успешный сценарий (happy path)
  - компенсацию при сбое платежа
  - разомкнутый автоматический выключатель
  - паттерн Outbox (сага не зависит от RabbitMQ)
  - идемпотентность платежа (защита от двойного списания при retry)
  - гонку cancel vs. сага (race condition fix)
  - автоматическую компенсацию при истечении дедлайна
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from apps.orders.models import Order, SagaLog
from apps.orders.saga import OrderSagaOrchestrator
from apps.payments.models import Payment
from apps.common.exceptions import (
    TemporaryFailure,
    CircuitOpenError,
    SagaExpiredError,
    OrderStateError,
)
from apps.common.models import OutboxMessage


# ------------------------------------------------------------------ #
# Happy path
# ------------------------------------------------------------------ #

@pytest.mark.django_db(transaction=True)
class TestSagaHappyPath:
    def test_order_created_and_completed(self):
        with patch("apps.payments.gateway.charge", return_value="ext-abc123"):
            orchestrator = OrderSagaOrchestrator()
            order = orchestrator.execute(
                customer="alice@example.com",
                restaurant="Pizza Palace",
                amount=Decimal("29.99"),
            )

        assert order.status == Order.Status.COMPLETED

        logs = SagaLog.objects.filter(saga_id=orchestrator.saga_id)
        steps = {(l.step, l.status) for l in logs}
        assert ("create_order", "SUCCESS") in steps
        assert ("process_payment", "SUCCESS") in steps
        assert ("confirm_order", "SUCCESS") in steps

    def test_deadline_set_on_order_creation(self):
        # Каждый заказ должен получить дедлайн в будущем
        with patch("apps.payments.gateway.charge", return_value="ext-x"):
            orchestrator = OrderSagaOrchestrator()
            order = orchestrator.execute("d@d.com", "R", Decimal("5.00"))

        assert order.deadline_at is not None
        assert order.deadline_at > timezone.now()

    def test_outbox_message_created_instead_of_direct_delay(self):
        # После успешного завершения уведомление должно быть в Outbox,
        # а не отправлено напрямую в RabbitMQ через .delay()
        with patch("apps.payments.gateway.charge", return_value="ext-outbox"):
            orchestrator = OrderSagaOrchestrator()
            order = orchestrator.execute("outbox@test.com", "R", Decimal("10.00"))

        msg = OutboxMessage.objects.filter(
            task_name="apps.notifications.tasks.send_notification_task",
            status=OutboxMessage.Status.PENDING,
        ).first()
        assert msg is not None
        assert msg.payload["recipient"] == "outbox@test.com"


# ------------------------------------------------------------------ #
# Сбой платежа → компенсация
# ------------------------------------------------------------------ #

@pytest.mark.django_db(transaction=True)
class TestSagaPaymentFailure:
    def test_order_cancelled_on_payment_failure(self):
        with patch(
            "apps.payments.gateway.charge",
            side_effect=TemporaryFailure("шлюз недоступен"),
        ):
            orchestrator = OrderSagaOrchestrator()
            with pytest.raises(TemporaryFailure):
                orchestrator.execute("bob@example.com", "Burger Barn", Decimal("15.00"))

        saga_id = orchestrator.saga_id
        assert SagaLog.objects.filter(saga_id=saga_id, status="COMPENSATED").exists()

        create_log = SagaLog.objects.filter(saga_id=saga_id, step="create_order").first()
        order = Order.objects.get(pk=create_log.payload["order_id"])
        assert order.status == Order.Status.CANCELLED


# ------------------------------------------------------------------ #
# Разомкнутый выключатель → компенсация
# ------------------------------------------------------------------ #

@pytest.mark.django_db(transaction=True)
class TestSagaCircuitBreakerOpen:
    def test_order_cancelled_when_circuit_open(self):
        with patch(
            "apps.payments.gateway.charge",
            side_effect=CircuitOpenError("выключатель разомкнут"),
        ):
            orchestrator = OrderSagaOrchestrator()
            with pytest.raises(CircuitOpenError):
                orchestrator.execute("carol@example.com", "Sushi Stop", Decimal("45.00"))

        create_log = SagaLog.objects.filter(
            saga_id=orchestrator.saga_id, step="create_order"
        ).first()
        order = Order.objects.get(pk=create_log.payload["order_id"])
        assert order.status == Order.Status.CANCELLED


# ------------------------------------------------------------------ #
# Transactional Outbox — сага не зависит от брокера
# ------------------------------------------------------------------ #

@pytest.mark.django_db(transaction=True)
class TestSagaOutboxPattern:
    def test_saga_completes_regardless_of_broker_state(self):
        """
        С Outbox сага не вызывает .delay() напрямую, поэтому недоступность
        RabbitMQ не влияет на успех execute(). Уведомление хранится в БД
        и будет доставлено позже через flush_outbox.
        """
        with patch("apps.payments.gateway.charge", return_value="ext-xyz"):
            orchestrator = OrderSagaOrchestrator()
            # Нет мока RabbitMQ — сага всё равно должна завершиться успешно
            order = orchestrator.execute("dave@example.com", "Noodle House", Decimal("20.00"))

        assert order.status == Order.Status.COMPLETED

        msg = OutboxMessage.objects.filter(
            task_name="apps.notifications.tasks.send_notification_task",
        ).first()
        assert msg is not None, "Уведомление должно быть записано в outbox"
        assert msg.payload["recipient"] == "dave@example.com"
        assert msg.status == OutboxMessage.Status.PENDING


# ------------------------------------------------------------------ #
# Идемпотентность платежа
# ------------------------------------------------------------------ #

@pytest.mark.django_db(transaction=True)
class TestSagaIdempotency:
    def test_payment_not_duplicated_on_retry(self):
        """
        При повторном вызове _step_process_payment (имитация Celery retry)
        шлюз не вызывается, если платёж уже был проведён успешно.
        """
        orchestrator = OrderSagaOrchestrator()

        with patch("apps.payments.gateway.charge", return_value="ext-idempotent") as mock_charge:
            order = orchestrator.execute("idem@test.com", "R", Decimal("10.00"))

        assert mock_charge.call_count == 1
        assert Payment.objects.filter(order_id=order.pk, status="SUCCESS").count() == 1

        # Имитируем повторный запуск задачи оплаты (Celery at-least-once)
        fresh_order = Order.objects.get(pk=order.pk)
        with patch("apps.payments.gateway.charge") as mock_charge2:
            ext_id = OrderSagaOrchestrator(
                saga_id=orchestrator.saga_id
            )._step_process_payment(fresh_order)

            mock_charge2.assert_not_called()

        assert ext_id == "ext-idempotent"
        # Второй платёж не создан
        assert Payment.objects.filter(order_id=order.pk, status="SUCCESS").count() == 1


# ------------------------------------------------------------------ #
# Race condition: cancel vs. сага
# ------------------------------------------------------------------ #

@pytest.mark.django_db(transaction=True)
class TestSagaRaceCondition:
    def test_cancel_blocked_when_order_is_processing(self):
        """
        Заказ в статусе PROCESSING захвачен сагой.
        Попытка отмены через API должна вернуть 409, статус не изменится.
        """
        from django.test import RequestFactory
        from apps.orders.views import cancel_order

        order = Order.objects.create(
            customer="race@test.com",
            restaurant="R",
            amount=Decimal("10.00"),
            status=Order.Status.PROCESSING,
            deadline_at=timezone.now() + timedelta(minutes=30),
        )

        factory = RequestFactory()
        request = factory.post(f"/api/orders/{order.pk}/cancel")
        response = cancel_order(request, order_id=order.pk)

        assert response.status_code == 409
        order.refresh_from_db()
        assert order.status == Order.Status.PROCESSING

    def test_cancel_succeeds_for_pending_order(self):
        from django.test import RequestFactory
        from apps.orders.views import cancel_order

        order = Order.objects.create(
            customer="pending@test.com",
            restaurant="R",
            amount=Decimal("10.00"),
            status=Order.Status.PENDING,
            deadline_at=timezone.now() + timedelta(minutes=30),
        )

        factory = RequestFactory()
        request = factory.post(f"/api/orders/{order.pk}/cancel")
        response = cancel_order(request, order_id=order.pk)

        assert response.status_code == 200
        order.refresh_from_db()
        assert order.status == Order.Status.CANCELLED

    def test_saga_raises_on_cancelled_order(self):
        """
        Если пользователь успел отменить заказ до того, как сага захватила его,
        _step_process_payment должна поднять OrderStateError.
        """
        order = Order.objects.create(
            customer="cancelled@test.com",
            restaurant="R",
            amount=Decimal("10.00"),
            status=Order.Status.CANCELLED,
            deadline_at=timezone.now() + timedelta(minutes=30),
        )

        with pytest.raises(OrderStateError):
            OrderSagaOrchestrator()._step_process_payment(order)


# ------------------------------------------------------------------ #
# Дедлайн саги
# ------------------------------------------------------------------ #

@pytest.mark.django_db(transaction=True)
class TestSagaDeadline:
    def test_expired_order_raises_saga_expired_error(self):
        """
        Если дедлайн уже истёк в момент попытки списания,
        сага бросает SagaExpiredError, не обращаясь к шлюзу.
        """
        order = Order.objects.create(
            customer="expired@test.com",
            restaurant="R",
            amount=Decimal("10.00"),
            status=Order.Status.PENDING,
            deadline_at=timezone.now() - timedelta(seconds=1),  # дедлайн в прошлом
        )

        with patch("apps.payments.gateway.charge") as mock_charge:
            with pytest.raises(SagaExpiredError):
                OrderSagaOrchestrator()._step_process_payment(order)

            mock_charge.assert_not_called()

    def test_full_execute_compensates_on_expired_deadline(self):
        """
        При истечении дедлайна полный цикл execute() должен компенсировать заказ.
        """
        orchestrator = OrderSagaOrchestrator()

        # Перехватываем создание заказа и сразу ставим дедлайн в прошлое
        original_create = orchestrator._step_create_order

        def create_with_expired_deadline(customer, restaurant, amount):
            order = original_create(customer, restaurant, amount)
            Order.objects.filter(pk=order.pk).update(
                deadline_at=timezone.now() - timedelta(seconds=1)
            )
            order.deadline_at = timezone.now() - timedelta(seconds=1)
            return order

        orchestrator._step_create_order = create_with_expired_deadline

        with pytest.raises(SagaExpiredError):
            orchestrator.execute("exp2@test.com", "R", Decimal("10.00"))

        create_log = SagaLog.objects.filter(
            saga_id=orchestrator.saga_id, step="create_order", status="SUCCESS"
        ).first()
        assert create_log is not None
        order = Order.objects.get(pk=create_log.payload["order_id"])
        assert order.status == Order.Status.CANCELLED
