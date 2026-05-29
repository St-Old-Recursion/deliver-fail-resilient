from __future__ import annotations

import logging
from celery import Task, shared_task

from apps.common.exceptions import TemporaryFailure

logger = logging.getLogger(__name__)

# Задержки между повторными попытками: 10с → 20с → 40с → 80с → 160с (экспоненциальный откат)
_COUNTDOWN = [10, 20, 40, 80, 160]


class _DLQTask(Task):
    """ 
    Dead Letter Queue.
    """

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        from apps.common.models import FailedTask
        FailedTask.objects.create(
            task_name=self.name,
            task_id=str(task_id),
            payload=kwargs,
            exception=str(exc),
        )
        logger.error(
            "%s окончательно упала (все попытки исчерпаны): %s", self.name, exc
        )


@shared_task(
    bind=True,
    base=_DLQTask,
    queue="payments",
    max_retries=5,
    autoretry_for=(TemporaryFailure,),
    retry_backoff=True,
    retry_backoff_max=160,
    retry_jitter=True,
    acks_late=True,
)
def process_payment_task(self, saga_id: str, order_id: int, amount: float) -> dict:
    """
    При TemporaryFailure выполняются повторные попытки с экспоненциальной задержкой (до 5 раз).
    """
    from apps.orders.saga import OrderSagaOrchestrator

    logger.info("process_payment_task попытка=%d сага=%s", self.request.retries, saga_id)
    orchestrator = OrderSagaOrchestrator(saga_id=saga_id)
    orchestrator.run_payment_step(order_id=order_id, amount=amount)
    return {"saga_id": saga_id, "status": "payment_done"}
