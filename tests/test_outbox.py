"""
Тесты паттерна Transactional Outbox.

Проверяем:
  1. Уведомление пишется в OutboxMessage в той же транзакции, что и заказ.
  2. flush_outbox отправляет PENDING-сообщения в Celery и помечает их SENT.
  3. flush_outbox не переотправляет уже SENT-сообщения (идемпотентность).
  4. При падении flush_outbox сообщения остаются PENDING (не теряются).
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock

from apps.common.models import OutboxMessage


# ------------------------------------------------------------------ #
# Outbox создаётся сагой
# ------------------------------------------------------------------ #

@pytest.mark.django_db(transaction=True)
class TestOutboxCreatedBySaga:
    def test_outbox_message_created_on_saga_success(self):
        from apps.orders.saga import OrderSagaOrchestrator

        with patch("apps.payments.gateway.charge", return_value="ext-ob1"):
            orchestrator = OrderSagaOrchestrator()
            orchestrator.execute("ob@test.com", "R", Decimal("10.00"))

        msgs = OutboxMessage.objects.filter(
            task_name="apps.notifications.tasks.send_notification_task"
        )
        assert msgs.count() == 1
        assert msgs.first().status == OutboxMessage.Status.PENDING

    def test_outbox_not_created_on_saga_failure(self):
        from apps.orders.saga import OrderSagaOrchestrator
        from apps.common.exceptions import TemporaryFailure

        with patch("apps.payments.gateway.charge", side_effect=TemporaryFailure("сбой")):
            with pytest.raises(TemporaryFailure):
                OrderSagaOrchestrator().execute("fail@test.com", "R", Decimal("10.00"))

        # При сбое оплаты уведомление не должно попасть в outbox
        assert OutboxMessage.objects.count() == 0

    def test_outbox_payload_contains_notification_data(self):
        from apps.orders.saga import OrderSagaOrchestrator

        with patch("apps.payments.gateway.charge", return_value="ext-ob2"):
            orchestrator = OrderSagaOrchestrator()
            orchestrator.execute("payload@test.com", "Кафе", Decimal("25.00"))

        msg = OutboxMessage.objects.first()
        assert msg.payload["recipient"] == "payload@test.com"
        assert msg.payload["notification_type"] == "email"
        assert "notification_id" in msg.payload
        assert "message" in msg.payload


# ------------------------------------------------------------------ #
# flush_outbox отправляет задачи
# ------------------------------------------------------------------ #

@pytest.mark.django_db(transaction=True)
class TestFlushOutbox:
    def test_sends_pending_messages_to_celery(self):
        from apps.common.tasks import flush_outbox

        OutboxMessage.objects.create(
            task_name="apps.notifications.tasks.send_notification_task",
            payload={
                "notification_id": 1,
                "recipient": "flush@test.com",
                "notification_type": "email",
                "message": "тест",
            },
        )

        with patch("celery.current_app.send_task") as mock_send:
            result = flush_outbox()

        assert result["sent"] == 1
        mock_send.assert_called_once_with(
            "apps.notifications.tasks.send_notification_task",
            kwargs={
                "notification_id": 1,
                "recipient": "flush@test.com",
                "notification_type": "email",
                "message": "тест",
            },
        )

        msg = OutboxMessage.objects.first()
        assert msg.status == OutboxMessage.Status.SENT

    def test_does_not_resend_already_sent_messages(self):
        from apps.common.tasks import flush_outbox

        # Сообщение уже отправлено
        OutboxMessage.objects.create(
            task_name="apps.notifications.tasks.send_notification_task",
            payload={"notification_id": 2, "recipient": "old@test.com",
                     "notification_type": "email", "message": "x"},
            status=OutboxMessage.Status.SENT,
        )

        with patch("celery.current_app.send_task") as mock_send:
            result = flush_outbox()

        assert result["sent"] == 0
        mock_send.assert_not_called()

    def test_returns_zero_when_no_pending_messages(self):
        from apps.common.tasks import flush_outbox

        with patch("celery.current_app.send_task") as mock_send:
            result = flush_outbox()

        assert result["sent"] == 0
        mock_send.assert_not_called()

    def test_messages_remain_pending_on_send_failure(self):
        """
        При сбое отправки в Celery транзакция откатывается,
        сообщения остаются в статусе PENDING — не теряются.
        """
        from apps.common.tasks import flush_outbox

        OutboxMessage.objects.create(
            task_name="apps.notifications.tasks.send_notification_task",
            payload={"notification_id": 3, "recipient": "r@t.com",
                     "notification_type": "email", "message": "x"},
        )

        with patch("celery.current_app.send_task", side_effect=Exception("Celery недоступен")):
            with pytest.raises(Exception, match="Celery недоступен"):
                flush_outbox()

        # Сообщение должно остаться PENDING (транзакция откатилась)
        msg = OutboxMessage.objects.first()
        assert msg.status == OutboxMessage.Status.PENDING

    def test_processes_at_most_50_messages_per_call(self):
        from apps.common.tasks import flush_outbox

        for i in range(60):
            OutboxMessage.objects.create(
                task_name="apps.notifications.tasks.send_notification_task",
                payload={"notification_id": i, "recipient": f"r{i}@t.com",
                         "notification_type": "email", "message": "x"},
            )

        with patch("celery.current_app.send_task"):
            result = flush_outbox()

        # За один вызов обрабатывается не более 50 сообщений
        assert result["sent"] == 50
        assert OutboxMessage.objects.filter(status=OutboxMessage.Status.PENDING).count() == 10
