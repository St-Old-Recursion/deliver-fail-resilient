"""
Тесты фоновой задачи cleanup_expired_sagas.

Проверяем:
  - PENDING-заказы с истёкшим дедлайном отменяются
  - PAID-заказы с истёкшим дедлайном отменяются с возвратом средств
  - PROCESSING-заказы с истёкшим дедлайном отменяются
  - Заказы с актуальным дедлайном не трогаются
  - В SagaLog создаётся запись о компенсации
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from datetime import timedelta

from django.utils import timezone

from apps.orders.models import Order, SagaLog
from apps.payments.models import Payment


def make_order(status: str, expired: bool, **kwargs) -> Order:
    deadline = (
        timezone.now() - timedelta(seconds=1) if expired
        else timezone.now() + timedelta(minutes=30)
    )
    return Order.objects.create(
        customer=kwargs.get("customer", "t@t.com"),
        restaurant=kwargs.get("restaurant", "R"),
        amount=kwargs.get("amount", Decimal("10.00")),
        status=status,
        deadline_at=deadline,
    )


@pytest.mark.django_db(transaction=True)
class TestCleanupExpiredSagas:
    def test_expired_pending_order_cancelled(self):
        from apps.orders.tasks import cleanup_expired_sagas

        order = make_order(Order.Status.PENDING, expired=True)
        result = cleanup_expired_sagas()

        assert result["cancelled"] >= 1
        order.refresh_from_db()
        assert order.status == Order.Status.CANCELLED

    def test_expired_processing_order_cancelled(self):
        from apps.orders.tasks import cleanup_expired_sagas

        order = make_order(Order.Status.PROCESSING, expired=True)
        result = cleanup_expired_sagas()

        assert result["cancelled"] >= 1
        order.refresh_from_db()
        assert order.status == Order.Status.CANCELLED

    def test_expired_paid_order_refunded_and_cancelled(self):
        from apps.orders.tasks import cleanup_expired_sagas

        order = make_order(Order.Status.PAID, expired=True, amount=Decimal("50.00"))
        payment = Payment.objects.create(
            order_id=order.pk,
            amount=Decimal("50.00"),
            status=Payment.Status.SUCCESS,
            external_payment_id="ext-refund-me",
        )

        result = cleanup_expired_sagas()

        assert result["refunded"] >= 1
        assert result["cancelled"] >= 1

        order.refresh_from_db()
        assert order.status == Order.Status.CANCELLED

        payment.refresh_from_db()
        assert payment.status == Payment.Status.REFUNDED

    def test_non_expired_order_not_touched(self):
        from apps.orders.tasks import cleanup_expired_sagas

        order = make_order(Order.Status.PENDING, expired=False)
        cleanup_expired_sagas()

        order.refresh_from_db()
        assert order.status == Order.Status.PENDING

    def test_completed_order_not_touched(self):
        # Завершённые заказы не должны попадать под компенсацию даже с истёкшим дедлайном
        from apps.orders.tasks import cleanup_expired_sagas

        order = make_order(Order.Status.COMPLETED, expired=True)
        cleanup_expired_sagas()

        order.refresh_from_db()
        assert order.status == Order.Status.COMPLETED

    def test_saga_log_created_for_each_cancelled_order(self):
        from apps.orders.tasks import cleanup_expired_sagas

        order = make_order(Order.Status.PENDING, expired=True)
        cleanup_expired_sagas()

        log = SagaLog.objects.filter(
            step="saga_timeout",
            status=SagaLog.StepStatus.COMPENSATED,
            payload__order_id=order.pk,
        ).first()
        assert log is not None

    def test_multiple_expired_orders_all_cancelled(self):
        from apps.orders.tasks import cleanup_expired_sagas

        orders = [make_order(Order.Status.PENDING, expired=True) for _ in range(3)]
        result = cleanup_expired_sagas()

        assert result["cancelled"] >= 3
        for order in orders:
            order.refresh_from_db()
            assert order.status == Order.Status.CANCELLED

    def test_returns_zero_when_nothing_expired(self):
        from apps.orders.tasks import cleanup_expired_sagas

        make_order(Order.Status.PENDING, expired=False)
        result = cleanup_expired_sagas()

        assert result["cancelled"] == 0
        assert result["refunded"] == 0
