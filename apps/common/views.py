from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from .circuit_breaker import all_breaker_statuses
from .circuit_breaker_redis import RedisCircuitBreaker
from apps.orders.models import SagaLog

_KNOWN_REDIS_BREAKERS = [
    {"name": "payment_gateway", "failure_threshold": 3, "window_seconds": 10.0, "reset_timeout": 30.0},
]


@require_GET
def circuit_breaker_status(request):
    redis_statuses = [
        RedisCircuitBreaker(**cfg).status
        for cfg in _KNOWN_REDIS_BREAKERS
    ]
    return JsonResponse({"breakers": all_breaker_statuses() + redis_statuses})


@require_GET
def saga_logs(request):
    saga_id = request.GET.get('saga_id', '')
    if saga_id:
        saga = SagaLog.objects.filter(saga_id=saga_id).values(
            "id", "saga_id", "step", "status", "error", "created_at"
        )
    else:
        saga = SagaLog.objects.order_by("-created_at").values(
            "id", "saga_id", "step", "status", "error", "created_at"
        )[:100]
    
    return JsonResponse({"logs": list(saga)})


@require_GET
def failure_counters(request):
    from apps.payments.gateway import _failure_counter
    return JsonResponse({"payment_gateway_failures": _failure_counter})


@csrf_exempt
@require_POST
def inject_failure(request):
    """Принудительно включает CHAOS_MODE в текущем процессе (только для разработки и тестирования)."""
    import json
    from django.conf import settings

    body = json.loads(request.body or b"{}")
    step = body.get("step", "payment")
    settings.CHAOS_MODE = True
    return JsonResponse({"injected": True, "step": step})
