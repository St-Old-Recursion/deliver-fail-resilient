import django
from django.conf import settings


def pytest_configure():
    """Override DB to SQLite for unit tests that don't need Postgres."""
    if not settings.configured:
        return
    # Allow override via DATABASE_URL env; fallback to sqlite for fast local runs
    if "sqlite" not in settings.DATABASES["default"]["ENGINE"]:
        pass  # keep postgres when DATABASE_URL points to real db
