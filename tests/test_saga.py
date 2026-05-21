"""
Интеграционные тесты оркестратора OrderSaga.

Запуск: pytest (требуется работающий PostgreSQL; в CI использовать docker-compose).
Тесты проверяют как успешный сценарий прохождения саги, так и обработку сбоев
с выполнением компенсирующих транзакций.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch
from decimal import Decimal

from apps.orders.models import Order, SagaLog
from apps.orders.saga import OrderSagaOrchestrator
from apps.common.exceptions import TemporaryFailure, CircuitOpenError


@pytest.mark.django_db(transaction=True)
class TestSagaHappyPath:
    def test_order_created_and_completed(self):
        # Подменяем вызов шлюза — возвращаем фиктивный идентификатор платежа
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
        # Проверяем, что все три шага саги завершились успешно
        assert ("create_order", "SUCCESS") in steps
        assert ("process_payment", "SUCCESS") in steps
        assert ("confirm_order", "SUCCESS") in steps


@pytest.mark.django_db(transaction=True)
class TestSagaPaymentFailure:
    def test_order_cancelled_on_payment_failure(self):
        # Имитируем недоступность платёжного шлюза
        with patch(
            "apps.payments.gateway.charge",
            side_effect=TemporaryFailure("шлюз недоступен"),
        ):
            orchestrator = OrderSagaOrchestrator()
            with pytest.raises(TemporaryFailure):
                orchestrator.execute(
                    customer="bob@example.com",
                    restaurant="Burger Barn",
                    amount=Decimal("15.00"),
                )

        # Сага должна выполнить компенсацию и отменить заказ
        saga_id = orchestrator.saga_id
        compensated = SagaLog.objects.filter(
            saga_id=saga_id, status="COMPENSATED"
        ).exists()
        assert compensated, "Ожидалась запись в SagaLog со статусом COMPENSATED"

        # Находим заказ через лог и проверяем его статус
        create_log = SagaLog.objects.filter(saga_id=saga_id, step="create_order").first()
        assert create_log is not None
        order_id = create_log.payload.get("order_id")
        order = Order.objects.get(pk=order_id)
        assert order.status == Order.Status.CANCELLED


@pytest.mark.django_db(transaction=True)
class TestSagaCircuitBreakerOpen:
    def test_order_cancelled_when_circuit_open(self):
        # Имитируем разомкнутый автоматический выключатель
        with patch(
            "apps.payments.gateway.charge",
            side_effect=CircuitOpenError("выключатель разомкнут"),
        ):
            orchestrator = OrderSagaOrchestrator()
            with pytest.raises(CircuitOpenError):
                orchestrator.execute(
                    customer="carol@example.com",
                    restaurant="Sushi Stop",
                    amount=Decimal("45.00"),
                )

        create_log = SagaLog.objects.filter(
            saga_id=orchestrator.saga_id, step="create_order"
        ).first()
        order = Order.objects.get(pk=create_log.payload["order_id"])
        assert order.status == Order.Status.CANCELLED


@pytest.mark.django_db(transaction=True)
class TestSagaBrokerUnavailable:
    """
    Имитация недоступности брокера сообщений (RabbitMQ).

    Метод delay() подменяется заглушкой, которая выбрасывает исключение.
    Проверяем, что ошибка при постановке уведомления в очередь не
    «проглатывается» молча, а корректно поднимается вверх по стеку.
    """

    def test_notification_enqueue_failure_does_not_break_saga(self):
        with patch("apps.payments.gateway.charge", return_value="ext-xyz"):
            with patch(
                "apps.notifications.tasks.send_notification_task.delay",
                side_effect=Exception("брокер недоступен"),
            ):
                orchestrator = OrderSagaOrchestrator()
                # Сбой постановки уведомления в очередь должен явно подняться наверх
                with pytest.raises(Exception, match="брокер недоступен"):
                    orchestrator.execute(
                        customer="dave@example.com",
                        restaurant="Noodle House",
                        amount=Decimal("20.00"),
                    )
