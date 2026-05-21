"""
Заглушка внешнего платёжного шлюза.

В рамках дипломного проекта полноценная интеграция с реальной платёжной системой
не предусматривается. Вместо этого реализована имитация: при включённом CHAOS_MODE
шлюз с заданной вероятностью (~30%) выбрасывает исключение TemporaryFailure.
Все вызовы защищены автоматическим выключателем (Circuit Breaker).
"""
import uuid
from apps.common.chaos import maybe_fail
from apps.common.circuit_breaker import get_breaker
from apps.common.exceptions import TemporaryFailure

# Простой счётчик сбоев в рамках процесса — отображается на /debug/failures/
_failure_counter: int = 0

_breaker = get_breaker(
    "payment_gateway",
    failure_threshold=3,
    window_seconds=10.0,
    reset_timeout=30.0,
)


def charge(order_id: int, amount: float) -> str:
    """Обращается к внешнему шлюзу; возвращает external_payment_id при успехе."""

    def _do_charge() -> str:
        maybe_fail()
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
