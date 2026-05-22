class TemporaryFailure(Exception):
    """Имитация временной недоступности внешнего сервиса (например, платёжного шлюза)."""


class CircuitOpenError(Exception):
    """Вызывается, когда автоматический выключатель разомкнут и запрос отклонён."""


class SagaExpiredError(Exception):
    """Сага превысила дедлайн — запускается компенсация."""


class OrderStateError(Exception):
    """Заказ находится в статусе, не допускающем данного перехода."""
