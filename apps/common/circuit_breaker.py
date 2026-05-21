"""
Реализация паттерна «Автоматический выключатель» (Circuit Breaker) без сторонних библиотек.

Данный паттерн является одним из ключевых механизмов отказоустойчивости в распределённых
системах. В дипломном проекте он применяется для защиты вызовов к платёжному шлюзу.

Состояния автоматического выключателя:
  CLOSED   – нормальная работа; ошибки подсчитываются в скользящем окне.
  OPEN     – вызовы немедленно отклоняются на время `reset_timeout` секунд.
  HALF     – разрешается один пробный вызов; успех → CLOSED, ошибка → OPEN снова.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, TypeVar

from .exceptions import CircuitOpenError, TemporaryFailure

T = TypeVar("T")


class State(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF = "HALF_OPEN"


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 3       # количество ошибок в окне для перехода в OPEN
    window_seconds: float = 10.0     # ширина скользящего окна подсчёта ошибок (сек.)
    reset_timeout: float = 30.0      # время ожидания в состоянии OPEN перед HALF_OPEN (сек.)

    _state: State = field(default=State.CLOSED, init=False, repr=False)
    _failures: list[float] = field(default_factory=list, init=False, repr=False)
    _opened_at: float | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    # ------------------------------------------------------------------ #
    # Публичный интерфейс
    # ------------------------------------------------------------------ #

    def call(self, func: Callable[[], T]) -> T:
        with self._lock:
            self._maybe_transition()
            if self._state == State.OPEN:
                raise CircuitOpenError(
                    f"Выключатель '{self.name}' разомкнут — вызовы отклоняются до "
                    f"{self._opened_at + self.reset_timeout:.0f}"
                )

        try:
            result = func()
        except (TemporaryFailure, Exception) as exc:
            with self._lock:
                self._record_failure()
            raise
        else:
            with self._lock:
                self._record_success()
            return result

    @property
    def status(self) -> dict:
        with self._lock:
            self._maybe_transition()
            recent = self._recent_failures()
            return {
                "name": self.name,
                "state": self._state.value,
                "recent_failures": len(recent),
                "failure_threshold": self.failure_threshold,
                "window_seconds": self.window_seconds,
                "reset_timeout": self.reset_timeout,
                "opened_at": self._opened_at,
                "reopens_at": (
                    round(self._opened_at + self.reset_timeout, 2)
                    if self._opened_at else None
                ),
            }

    # ------------------------------------------------------------------ #
    # Вспомогательные методы (вызывать только под self._lock)
    # ------------------------------------------------------------------ #

    def _maybe_transition(self) -> None:
        # Проверяем, не истёк ли таймаут ожидания — если да, переходим в HALF_OPEN
        if self._state == State.OPEN:
            if time.monotonic() >= self._opened_at + self.reset_timeout:
                self._state = State.HALF
                self._opened_at = None

    def _recent_failures(self) -> list[float]:
        # Отфильтровываем устаревшие ошибки, вышедшие за пределы скользящего окна
        cutoff = time.monotonic() - self.window_seconds
        self._failures = [t for t in self._failures if t >= cutoff]
        return self._failures

    def _record_failure(self) -> None:
        self._failures.append(time.monotonic())
        if len(self._recent_failures()) >= self.failure_threshold:
            self._state = State.OPEN
            self._opened_at = time.monotonic()

    def _record_success(self) -> None:
        self._failures.clear()
        self._state = State.CLOSED
        self._opened_at = None


# Реестр экземпляров: один выключатель на именованный сервис, общий для всех потоков.
_registry: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def get_breaker(name: str, **kwargs) -> CircuitBreaker:
    with _registry_lock:
        if name not in _registry:
            _registry[name] = CircuitBreaker(name=name, **kwargs)
        return _registry[name]


def all_breaker_statuses() -> list[dict]:
    with _registry_lock:
        return [cb.status for cb in _registry.values()]
