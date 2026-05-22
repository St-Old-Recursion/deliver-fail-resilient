"""
Заглушка внешнего платёжного шлюза.

Исправление идемпотентности: принимает idempotency_key (= saga_id).
UUID5 детерминирован — один и тот же ключ всегда даёт один и тот же external_payment_id,
что имитирует поведение реальных шлюзов (Stripe, YooKassa и др.).
Все вызовы защищены Redis-based Circuit Breaker.
"""
import uuid
from apps.common.chaos import maybe_fail
from apps.common.circuit_breaker_redis import get_redis_breaker
from apps.common.exceptions import TemporaryFailure

# Простой счётчик сбоев в рамках процесса — отображается на /debug/failures/
_failure_counter: int = 0

_breaker = get_redis_breaker(
    "payment_gateway",
    failure_threshold=3,
    window_seconds=10.0,
    reset_timeout=30.0,
)


def charge(order_id: int, amount: float, idempotency_key: str = "") -> str:
    """
    Обращается к внешнему шлюзу; возвращает external_payment_id при успехе.
    idempotency_key гарантирует, что повторный вызов с тем же ключом вернёт
    тот же ID, а не создаст второе списание.
    """

    def _do_charge() -> str:
        maybe_fail()
        if idempotency_key:
            # Детерминированный UUID — имитация idempotency key шлюза
            return f"ext-{uuid.uuid5(uuid.NAMESPACE_DNS, idempotency_key).hex[:12]}"
        return f"ext-{uuid.uuid4().hex[:12]}"

    try:
        return _breaker.call(_do_charge)
    except TemporaryFailure:
        global _failure_counter
        _failure_counter += 1
        raise


def refund(external_payment_id: str) -> None:
    """Имитация возврата платежа — компенсирующая транзакция, всегда успешна."""
    pass
