# Симулятор сервиса доставки — дипломный проект

Django-приложение на базе PostgreSQL + Redis + RabbitMQ + Celery.
Реализует паттерны отказоустойчивости: Orchestration Saga, Circuit Breaker, Transactional Outbox, Dead Letter Queue.

---


```bash
# Создать заказ (запускает полный цикл саги)
curl -s -X POST http://localhost:8000/api/orders/ \
  -H 'Content-Type: application/json' \
  -d '{"customer":"alice@example.com","restaurant":"Пицца Палас","amount":"29.99"}' | jq

# Проверить статус заказа
curl -s http://localhost:8000/api/orders/1/status | jq

# Отменить заказ (только PENDING; PROCESSING → 409 Conflict)
curl -s -X POST http://localhost:8000/api/orders/1/cancel | jq

# Журнал саги
curl -s http://localhost:8000/api/debug/saga-logs/ | jq

# Состояние Circuit Breaker
curl -s http://localhost:8000/api/debug/circuit-breaker/status | jq

# Счётчик сбоев шлюза
curl -s http://localhost:8000/api/debug/failures/ | jq

# Задачи в Dead Letter Queue
curl -s http://localhost:8000/api/debug/failed-tasks/ | jq

# Очередь Outbox (необработанные уведомления)
curl -s http://localhost:8000/api/debug/outbox/ | jq
```

---

## Тестирование отказоустойчивости

### 1. Хаос-тестирование (случайные сбои платежа)

```bash
# В файле .env:
CHAOS_MODE=true
CHAOS_FAILURE_PROBABILITY=0.3

docker-compose up --build -d

# Примерно каждый третий запрос завершится компенсацией (заказ → CANCELLED)
for i in $(seq 1 10); do
  curl -s -X POST http://localhost:8000/api/orders/ \
    -H 'Content-Type: application/json' \
    -d '{"customer":"test@test.com","restaurant":"Ресторан","amount":"10.00"}' | jq .status
done
```

### 2. Разомкнуть Circuit Breaker

Установить `CHAOS_FAILURE_PROBABILITY=1.0`, отправить 3+ запроса — выключатель перейдёт в OPEN:

```bash
curl -s http://localhost:8000/api/debug/circuit-breaker/status | jq
# "state": "OPEN"
```

Последующие запросы отклоняются мгновенно (HTTP 503), без обращения к шлюзу.
Через 30 секунд TTL истекает в Redis → HALF_OPEN → пробный вызов.

### 3. Проверить race condition fix

```bash
# Создать заказ
curl -s -X POST http://localhost:8000/api/orders/ \
  -H 'Content-Type: application/json' \
  -d '{"customer":"race@test.com","restaurant":"R","amount":"10.00"}' | jq

# Пока сага в PROCESSING — попытка отмены вернёт 409
curl -s -X POST http://localhost:8000/api/orders/1/cancel | jq
# {"error": "Заказ уже обрабатывается — отмена невозможна", "status": "PROCESSING"}
```

### 4. Проверить Transactional Outbox

```bash
# Создать заказ — OutboxMessage создастся в той же транзакции
curl -s -X POST http://localhost:8000/api/orders/ \
  -H 'Content-Type: application/json' \
  -d '{"customer":"outbox@test.com","restaurant":"R","amount":"10.00"}' | jq

# Посмотреть очередь outbox (flush_outbox запускается каждые 5 с)
curl -s http://localhost:8000/api/debug/outbox/ | jq
```

### 5. Проверить Dead Letter Queue

```bash
# Включить 100% сбои и дождаться исчерпания retry (max_retries=5)
# Задачи попадут в таблицу failed_tasks
curl -s http://localhost:8000/api/debug/failed-tasks/ | jq
```

### 6. Проверить saga TTL / cleanup

```bash
# Создать заказ с просроченным дедлайном (через тест или напрямую в БД)
# cleanup_expired_sagas запускается Celery Beat каждые 5 минут
# Журнал саги покажет запись saga_timeout / COMPENSATED
curl -s http://localhost:8000/api/debug/saga-logs/ | jq '.[] | select(.step=="saga_timeout")'
```

### 7. Остановить RabbitMQ — проверить durable очереди

```bash
docker-compose stop rabbitmq
# Отправить заказ — воркер не может получить задачу
docker-compose start rabbitmq
# Воркеры переподключатся и обработают накопившиеся задачи
```

Очереди **durable**, задачи `acks_late=True` — сообщения не теряются при перезапуске.

### 8. Инъекция сбоя через API

```bash
curl -s -X POST http://localhost:8000/api/debug/inject-failure/ \
  -H 'Content-Type: application/json' \
  -d '{"step":"payment"}'
```

---

## Запуск тестов

```bash
docker-compose run --rm web pytest tests/ -v
```

Покрытие:

| Файл | Что тестируется |
|---|---|
| `test_saga.py` | Happy path, сбой платежа, разомкнутый CB, Outbox, идемпотентность, race condition, дедлайн |
| `test_circuit_breaker.py` | In-memory CB: переходы CLOSED/OPEN/HALF_OPEN |
| `test_circuit_breaker_redis.py` | Redis CB: общее состояние между воркерами, восстановление через TTL |
| `test_outbox.py` | Outbox создаётся сагой, flush_outbox отправляет ≤50, rollback при сбое Celery |
| `test_cleanup.py` | cleanup_expired_sagas: PENDING/PROCESSING/PAID компенсируются, COMPLETED не трогается |

---

## Архитектурные решения

### Orchestration Saga (`apps/orders/saga.py`)

Центральный оркестратор управляет последовательностью шагов и компенсациями:

```
create_order → process_payment → confirm_order
                    ↑
        3 механизма защиты:
        1. идемпотентность
        2. race condition fix
        3. проверка дедлайна
```

Каждый шаг пишет строку в `SagaLog` (STARTED → SUCCESS / COMPENSATED).
При сбое запускаются компенсирующие транзакции в обратном порядке.

### Circuit Breaker — Redis-backed (`apps/common/circuit_breaker_redis.py`)

Состояние хранится в Redis — все воркеры видят **одно** общее состояние (нет split-brain):

| Redis-ключ | Содержимое | TTL |
|---|---|---|
| `cb:{name}:state` | `"OPEN"` | `reset_timeout` (30 с) |
| `cb:{name}:failures` | счётчик ошибок | `window_seconds` (10 с) |
| `cb:{name}:reopens_at` | timestamp | `reset_timeout` |

OPEN → HALF_OPEN: TTL `cb:{name}:state` истекает автоматически → следующий вызов пробный.

| Переход | Условие |
|---|---|
| CLOSED → OPEN | N ошибок за `window_seconds` |
| OPEN → HALF_OPEN | TTL истёк в Redis |
| HALF_OPEN → CLOSED | пробный вызов успешен |
| HALF_OPEN → OPEN | пробный вызов провалился |

### Идемпотентность платежа

Двухуровневая защита от двойного списания при Celery retry:

1. **DB check:** `Payment.objects.filter(order_id, status=SUCCESS).first()` — если есть, возвращаем `external_payment_id` без вызова шлюза.
2. **Idempotency key:** `saga_id` передаётся в `gateway.charge()`. UUID5 детерминирован — один ключ всегда даёт один `external_payment_id`.

### Race condition fix

Атомарный захват статуса заказа через `UPDATE ... WHERE status IN (PENDING, PROCESSING)`:

- Если заказ уже `CANCELLED` (пользователь успел отменить) — UPDATE вернёт 0 строк → `OrderStateError`.
- `cancel_order` обновляет только `PENDING`; если статус `PROCESSING` — HTTP 409 Conflict.

### Saga TTL / Deadline

- При создании: `deadline_at = now() + 30 мин`.
- В `_step_process_payment`: если `deadline_at < now()` → `SagaExpiredError`, шлюз не вызывается.
- `cleanup_expired_sagas` (Celery Beat, каждые 5 мин): находит просроченные заказы, компенсирует. PAID → `refund()` + `Payment(REFUNDED)`.

### Transactional Outbox (`apps/common/models.py`, `apps/common/tasks.py`)

Уведомление пишется в таблицу `OutboxMessage` **в той же транзакции**, что и `Order(COMPLETED)`:

```
transaction.atomic():
  ├── Order.status = COMPLETED
  └── OutboxMessage(task_name, payload, PENDING)

flush_outbox (каждые 5 с):
  SELECT FOR UPDATE SKIP LOCKED → send_task() → OutboxMessage(SENT)
```

Гарантирует: либо заказ завершён И уведомление в очереди, либо ни то ни другое.

### Dead Letter Queue (`apps/common/models.py`, `apps/payments/tasks.py`)

`_DLQTask(Task)` — базовый класс задачи. `on_failure` вызывается после исчерпания всех retry:

```python
FailedTask.objects.create(task_name, task_id, payload, exception)
```

Задача не теряется — доступна для ручного разбора и повторного запуска.

Retry-политика платёжных задач: `max_retries=5`, экспоненциальный откат до 160 с, `acks_late=True`.

### Очереди Celery

| Очередь | Воркер | Задачи |
|---|---|---|
| `payments` | worker-payments | `process_payment_task` |
| `notifications` | worker-notifications | `send_notification_task` |
| `system` | worker-system | `flush_outbox`, `cleanup_expired_sagas` |

Все очереди **durable**. Celery Beat запускает периодические задачи по расписанию.
