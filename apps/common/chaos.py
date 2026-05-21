"""Вспомогательные инструменты для хаос-тестирования (chaos engineering)."""
import random
from django.conf import settings
from .exceptions import TemporaryFailure


def maybe_fail(probability: float | None = None) -> None:
    """Вызывает TemporaryFailure с заданной вероятностью при включённом CHAOS_MODE."""
    if not settings.CHAOS_MODE:
        return
    p = probability if probability is not None else settings.CHAOS_FAILURE_PROBABILITY
    if random.random() < p:
        raise TemporaryFailure("Сбой, вызванный режимом хаос-тестирования")
