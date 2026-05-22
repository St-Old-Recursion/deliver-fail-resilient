from __future__ import annotations

import json
import logging
from decimal import Decimal

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import Order
from .saga import OrderSagaOrchestrator
from apps.common.exceptions import TemporaryFailure, CircuitOpenError, SagaExpiredError, OrderStateError

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def create_order(request):
    body = json.loads(request.body or b"{}")
    customer = body.get("customer", "anonymous")
    restaurant = body.get("restaurant", "unknown")
    amount = Decimal(str(body.get("amount", "0")))

    orchestrator = OrderSagaOrchestrator()
    try:
        order = orchestrator.execute(customer, restaurant, amount)
        return JsonResponse(
            {"id": order.pk, "status": order.status, "saga_id": orchestrator.saga_id},
            status=201,
        )
    except (TemporaryFailure, CircuitOpenError) as exc:
        # Сбой платёжного шлюза или разомкнутый выключатель — возвращаем 503
        return JsonResponse(
            {"error": str(exc), "saga_id": orchestrator.saga_id}, status=503
        )
    except SagaExpiredError as exc:
        return JsonResponse(
            {"error": str(exc), "saga_id": orchestrator.saga_id}, status=408
        )
    except Exception as exc:
        logger.exception("Непредвиденная ошибка в create_order")
        return JsonResponse({"error": str(exc)}, status=500)


@require_GET
def order_status(request, order_id: int):
    try:
        order = Order.objects.get(pk=order_id)
    except Order.DoesNotExist:
        return JsonResponse({"error": "Заказ не найден"}, status=404)
    return JsonResponse({"id": order.pk, "status": order.status, "amount": str(order.amount)})


@csrf_exempt
@require_POST
def cancel_order(request, order_id: int):
    # Отменяем только PENDING — PROCESSING уже захвачен сагой, отмена невозможна
    updated = Order.objects.filter(
        pk=order_id, status=Order.Status.PENDING
    ).update(status=Order.Status.CANCELLED)

    if updated:
        return JsonResponse({"id": order_id, "status": Order.Status.CANCELLED})

    # Разбираемся почему не обновилось
    try:
        order = Order.objects.get(pk=order_id)
    except Order.DoesNotExist:
        return JsonResponse({"error": "Заказ не найден"}, status=404)

    if order.status == Order.Status.PROCESSING:
        # 409 Conflict: сага уже захватила заказ, отмена заблокирована
        return JsonResponse(
            {"error": "Заказ уже обрабатывается — отмена невозможна", "status": order.status},
            status=409,
        )

    return JsonResponse(
        {"error": f"Отмена невозможна для статуса '{order.status}'", "status": order.status},
        status=400,
    )
