"""Celery application instance used for background agent execution."""

from __future__ import annotations

import os
from pathlib import Path

from celery import Celery
from dotenv import load_dotenv

def _should_load_local_env() -> bool:
    env = (os.getenv("ENV") or os.getenv("ENVIRONMENT") or "").lower()
    if env and env != "dev":
        return False
    return Path(".env").is_file()


if _should_load_local_env():  # Only load .env for local development runs
    load_dotenv()


def _default(str_env: str, fallback: str) -> str:
    value = os.getenv(str_env)
    return value if value else fallback


broker_url = _default("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend = _default("CELERY_RESULT_BACKEND", broker_url)

celery_app = Celery(
    "tmates-platform",
    broker=broker_url,
    backend=result_backend,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    broker_connection_retry_on_startup=True,
    task_track_started=True,
    worker_prefetch_multiplier=int(os.getenv("CELERY_WORKER_PREFETCH", "1")),
    task_default_queue=os.getenv("CELERY_DEFAULT_QUEUE", "agents"),
    timezone=os.getenv("CELERY_TIMEZONE", "UTC"),
    enable_utc=True,
)


__all__ = ["celery_app"]
