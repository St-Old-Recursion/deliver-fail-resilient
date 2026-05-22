import os
from celery import Celery
from kombu import Exchange, Queue

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("delivery")
app.config_from_object("django.conf:settings", namespace="CELERY")

# Персистентные очереди — сообщения сохраняются при перезапуске RabbitMQ
durable_exchange = Exchange("delivery", type="direct", durable=True)

app.conf.task_queues = [
    Queue("payments",      durable_exchange, routing_key="payments",      durable=True),
    Queue("notifications", durable_exchange, routing_key="notifications",  durable=True),
    # system — внутренние задачи: flush_outbox, cleanup_expired_sagas
    Queue("system",        durable_exchange, routing_key="system",         durable=True),
]
app.conf.task_default_queue = "payments"
app.conf.task_default_exchange = "delivery"
app.conf.task_default_routing_key = "payments"

app.autodiscover_tasks()
