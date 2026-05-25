import pytest


@pytest.fixture(autouse=True)
def reset_cache():
    """
    Очищаем Django-кэш перед каждым тестом.
    Нужно для изоляции тестов Redis Circuit Breaker:
    состояние выключателя хранится в кэше и не должно переходить между тестами.
    """
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def reset_cb_registry():
    """
    Очищаем реестр Redis Circuit Breaker между тестами.
    Без этого экземпляры из одного теста будут доступны в другом.
    """
    from apps.common.circuit_breaker_redis import _registry as redis_registry
    from apps.common.circuit_breaker import _registry as inmem_registry
    redis_registry.clear()
    inmem_registry.clear()
    yield
    redis_registry.clear()
    inmem_registry.clear()
