"""
Redis-backed Circuit Breaker — решает проблему split-brain при нескольких воркерах.

В отличие от in-memory реализации, все воркеры видят одно общее состояние через Redis.
Алгоритм тот же: CLOSED → OPEN (N ошибок за окно) → HALF_OPEN (через timeout) → CLOSED.

Используется в gateway.py вместо in-memory выключателя.
"""
from __future__ import annotations

import time
import logging
from typing import Callable, TypeVar

from django.core.cache import cache

from .exceptions import CircuitOpenError, TemporaryFailure

logger = logging.getLogger(__name__)
T = TypeVar("T")

_STATE_CLOSED = "CLOSED"
_STATE_OPEN = "OPEN"
_STATE_HALF = "HALF_OPEN"


class RedisCircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        window_seconds: float = 10.0,
        reset_timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.reset_timeout = reset_timeout

    # ------------------------------------------------------------------ #
    # Ключи в Redis
    # ------------------------------------------------------------------ #

    def _k(self, suffix: str) -> str:
        return f"cb:{self.name}:{suffix}"

    # ------------------------------------------------------------------ #
    # Публичный интерфейс
    # ------------------------------------------------------------------ #

    def call(self, func: Callable[[], T]) -> T:
        state = cache.get(self._k("state"), _STATE_CLOSED)

        if state == _STATE_OPEN:
            raise CircuitOpenError(
                f"Выключатель '{self.name}' разомкнут — вызовы отклоняются"
            )

        try:
            result = func()
        except Exception:
            self._record_failure()
            raise
        else:
            self._record_success()
            return result

    @property
    def status(self) -> dict:
        state = cache.get(self._k("state"), _STATE_CLOSED)
        failures = cache.get(self._k("failures"), 0)
        reopens_at = cache.get(self._k("reopens_at"))
        return {
            "name": self.name,
            "state": state,
            "recent_failures": failures,
            "failure_threshold": self.failure_threshold,
            "window_seconds": self.window_seconds,
            "reset_timeout": self.reset_timeout,
            "reopens_at": reopens_at,
        }

    # ------------------------------------------------------------------ #
    # Внутренние методы
    # ------------------------------------------------------------------ #

    def _record_failure(self) -> None:
        # Инкрементируем счётчик ошибок с TTL = window_seconds
        failures = (cache.get(self._k("failures")) or 0) + 1
        cache.set(self._k("failures"), failures, timeout=int(self.window_seconds))

        if failures >= self.failure_threshold:
            reopens_at = time.time() + self.reset_timeout
            cache.set(self._k("state"), _STATE_OPEN, timeout=int(self.reset_timeout))
            cache.set(self._k("reopens_at"), round(reopens_at, 2), timeout=int(self.reset_timeout))
            logger.warning("Выключатель '%s' разомкнут (%d ошибок)", self.name, failures)

    def _record_success(self) -> None:
        # Успешный вызов сбрасывает счётчик и закрывает выключатель
        cache.delete(self._k("state"))
        cache.delete(self._k("failures"))
        cache.delete(self._k("reopens_at"))


# Реестр экземпляров
_registry: dict[str, RedisCircuitBreaker] = {}


def get_redis_breaker(name: str, **kwargs) -> RedisCircuitBreaker:
    if name not in _registry:
        _registry[name] = RedisCircuitBreaker(name=name, **kwargs)
    return _registry[name]
