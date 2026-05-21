"""Модульные тесты автоматического выключателя (Circuit Breaker). БД не требуется."""
from __future__ import annotations

import time
import pytest
from unittest.mock import patch

from apps.common.circuit_breaker import CircuitBreaker, State
from apps.common.exceptions import CircuitOpenError, TemporaryFailure


class TestCircuitBreakerClosed:
    def test_passes_through_on_success(self):
        # В нормальном состоянии (CLOSED) вызов должен проходить без ошибок
        cb = CircuitBreaker(name="test-closed", failure_threshold=3, window_seconds=10)
        result = cb.call(lambda: 42)
        assert result == 42
        assert cb._state == State.CLOSED

    def test_opens_after_threshold_failures(self):
        # После достижения порога ошибок выключатель должен перейти в состояние OPEN
        cb = CircuitBreaker(name="test-open", failure_threshold=3, window_seconds=10)
        for _ in range(3):
            with pytest.raises(TemporaryFailure):
                cb.call(lambda: (_ for _ in ()).throw(TemporaryFailure("сбой")))
        assert cb._state == State.OPEN

    def test_rejects_when_open(self):
        # В состоянии OPEN выключатель должен отклонять вызовы без обращения к сервису
        cb = CircuitBreaker(name="test-reject", failure_threshold=1, window_seconds=10)
        with pytest.raises(TemporaryFailure):
            cb.call(lambda: (_ for _ in ()).throw(TemporaryFailure("сбой")))
        with pytest.raises(CircuitOpenError):
            cb.call(lambda: 42)


class TestCircuitBreakerHalfOpen:
    def test_transitions_to_half_open_after_timeout(self):
        # После истечения reset_timeout выключатель должен перейти в HALF_OPEN
        # и пропустить один пробный вызов
        cb = CircuitBreaker(
            name="test-half",
            failure_threshold=1,
            window_seconds=10,
            reset_timeout=0.05,  # 50 мс — для быстрого прохождения теста
        )
        with pytest.raises(TemporaryFailure):
            cb.call(lambda: (_ for _ in ()).throw(TemporaryFailure("сбой")))
        assert cb._state == State.OPEN

        time.sleep(0.06)
        # Пробный вызов в состоянии HALF_OPEN должен пройти и вернуть выключатель в CLOSED
        result = cb.call(lambda: "ok")
        assert result == "ok"
        assert cb._state == State.CLOSED
