from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from .circuit_breaker import all_breaker_statuses
from apps.orders.models import SagaLog


@require_GET
def circuit_breaker_status(request):
    return JsonResponse({"breakers": all_breaker_statuses()})


@require_GET
def saga_logs(request):
    logs = list(
        SagaLog.objects.order_by("-created_at").values(
            "id", "saga_id", "step", "status", "error", "created_at"
        )[:100]
    )
    return JsonResponse({"logs": logs})


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
