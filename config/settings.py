import environ
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    CHAOS_MODE=(bool, False),
    CHAOS_FAILURE_PROBABILITY=(float, 0.3),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost"])

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "apps.orders",
    "apps.payments",
    "apps.notifications",
    "apps.common",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL"),
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL"),
    }
}

# Celery
CELERY_BROKER_URL = env("RABBITMQ_URL")
CELERY_RESULT_BACKEND = env("REDIS_URL")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

CELERY_TASK_ROUTES = {
    "apps.payments.tasks.*":      {"queue": "payments"},
    "apps.notifications.tasks.*": {"queue": "notifications"},
    "apps.common.tasks.*":        {"queue": "system"},
    "apps.orders.tasks.*":        {"queue": "system"},
}

# Celery Beat — расписание фоновых задач
CELERY_BEAT_SCHEDULE = {
    # Transactional Outbox: перекладываем сообщения из БД в RabbitMQ каждые 5 секунд
    "flush-outbox": {
        "task": "apps.common.tasks.flush_outbox",
        "schedule": 5.0,
        "options": {"queue": "system"},
    },
    # Дедлайны саг: отменяем просроченные заказы каждые 5 минут
    "cleanup-expired-sagas": {
        "task": "apps.orders.tasks.cleanup_expired_sagas",
        "schedule": 300.0,
        "options": {"queue": "system"},
    },
}

# Chaos engineering
CHAOS_MODE: bool = env("CHAOS_MODE")
CHAOS_FAILURE_PROBABILITY: float = env("CHAOS_FAILURE_PROBABILITY")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en-us"
USE_TZ = True
