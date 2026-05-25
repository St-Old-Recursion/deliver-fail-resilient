"""
Модульные тесты Redis-based Circuit Breaker.

Используется LocMemCache вместо реального Redis — поведение идентично
(get/set/delete), но не требует запущенного Redis при юнит-тестировании.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch
from django.test import override_settings

from apps.common.circuit_breaker_redis import RedisCircuitBreaker, _registry
from apps.common.exceptions import CircuitOpenError, TemporaryFailure

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-cb",
    }
}


@pytest.fixture(autouse=True)
def clear_cache_and_registry():
    """Очищаем кэш и реестр выключателей перед каждым тестом."""
    from django.core.cache import cache
    cache.clear()
    _registry.clear()
    yield
    cache.clear()
    _registry.clear()


@override_settings(CACHES=LOCMEM_CACHE)
class TestRedisCBClosed:
    def test_passes_through_on_success(self):
        # В нормальном состоянии (CLOSED) вызов должен вернуть результат функции
        cb = RedisCircuitBreaker(name="cb-ok", failure_threshold=3, window_seconds=10)
        result = cb.call(lambda: 99)
        assert result == 99

    def test_state_closed_by_default(self):
        cb = RedisCircuitBreaker(name="cb-default", failure_threshold=3, window_seconds=10)
        status = cb.status
        assert status["state"] == "CLOSED"
        assert status["recent_failures"] == 0

    def test_success_resets_failure_counter(self):
        # Один сбой, затем успех — счётчик должен обнулиться
        cb = RedisCircuitBreaker(name="cb-reset", failure_threshold=5, window_seconds=10)
        with pytest.raises(TemporaryFailure):
            cb.call(lambda: (_ for _ in ()).throw(TemporaryFailure("разовый сбой")))

        assert cb.status["recent_failures"] == 1

        cb.call(lambda: "ok")
        assert cb.status["recent_failures"] == 0


@override_settings(CACHES=LOCMEM_CACHE)
class TestRedisCBOpens:
    def test_opens_after_threshold_failures(self):
        # После N сбоев подряд выключатель должен перейти в OPEN
        cb = RedisCircuitBreaker(name="cb-opens", failure_threshold=3, window_seconds=10)
        for _ in range(3):
            with pytest.raises(TemporaryFailure):
                cb.call(lambda: (_ for _ in ()).throw(TemporaryFailure("сбой")))

        assert cb.status["state"] == "OPEN"

    def test_rejects_calls_when_open(self):
        # В состоянии OPEN вызовы немедленно отклоняются без обращения к функции
        cb = RedisCircuitBreaker(name="cb-reject", failure_threshold=1, window_seconds=10)
        with pytest.raises(TemporaryFailure):
            cb.call(lambda: (_ for _ in ()).throw(TemporaryFailure("сбой")))

        called = False

        def side_effect():
            nonlocal called
            called = True
            return "should not reach"

        with pytest.raises(CircuitOpenError):
            cb.call(side_effect)

        assert not called, "Функция не должна вызываться в состоянии OPEN"

    def test_status_contains_reopens_at_when_open(self):
        cb = RedisCircuitBreaker(name="cb-status", failure_threshold=1, window_seconds=10, reset_timeout=30)
        with pytest.raises(TemporaryFailure):
            cb.call(lambda: (_ for _ in ()).throw(TemporaryFailure("сбой")))

        status = cb.status
        assert status["state"] == "OPEN"
        assert status["reopens_at"] is not None


@override_settings(CACHES=LOCMEM_CACHE)
class TestRedisCBRecovery:
    def test_probe_call_allowed_after_state_expires(self):
        """
        Состояние OPEN хранится в Redis/LocMem с TTL = reset_timeout.
        После истечения TTL cache.get возвращает None → CLOSED.
        Имитируем это явным удалением ключа из кэша (эквивалент истечения TTL).
        """
        from django.core.cache import cache

        cb = RedisCircuitBreaker(name="cb-recover", failure_threshold=1, window_seconds=10, reset_timeout=30)
        with pytest.raises(TemporaryFailure):
            cb.call(lambda: (_ for _ in ()).throw(TemporaryFailure("сбой")))

        assert cb.status["state"] == "OPEN"

        # Имитируем истечение TTL: удаляем ключ состояния из кэша
        cache.delete("cb:cb-recover:state")

        # Теперь выключатель должен пропустить пробный вызов
        result = cb.call(lambda: "восстановлен")
        assert result == "восстановлен"

    def test_probe_failure_reopens_circuit(self):
        """
        Если пробный вызов после «истечения TTL» снова проваливается,
        выключатель должен снова перейти в OPEN.
        """
        from django.core.cache import cache

        cb = RedisCircuitBreaker(name="cb-reopen", failure_threshold=1, window_seconds=10, reset_timeout=30)
        with pytest.raises(TemporaryFailure):
            cb.call(lambda: (_ for _ in ()).throw(TemporaryFailure("сбой")))

        cache.delete("cb:cb-reopen:state")

        # Пробный вызов снова падает
        with pytest.raises(TemporaryFailure):
            cb.call(lambda: (_ for _ in ()).throw(TemporaryFailure("пробный сбой")))

        assert cb.status["state"] == "OPEN"


@override_settings(CACHES=LOCMEM_CACHE)
class TestRedisCBDistributed:
    def test_two_instances_share_state(self):
        """
        Ключевое преимущество Redis CB над in-memory:
        два экземпляра с одним именем видят одно состояние (через общий кэш).
        """
        cb1 = RedisCircuitBreaker(name="cb-shared", failure_threshold=2, window_seconds=10)
        cb2 = RedisCircuitBreaker(name="cb-shared", failure_threshold=2, window_seconds=10)

        # cb1 накапливает ошибки
        for _ in range(2):
            with pytest.raises(TemporaryFailure):
                cb1.call(lambda: (_ for _ in ()).throw(TemporaryFailure("сбой")))

        # cb2 должен видеть OPEN — состояние общее
        with pytest.raises(CircuitOpenError):
            cb2.call(lambda: "не должно дойти")
