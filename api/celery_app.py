from __future__ import annotations

import os

from celery import Celery

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv() -> None:
        return None


load_dotenv()

broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend = os.getenv("CELERY_RESULT_BACKEND", broker_url)

celery_app = Celery("smiley", broker=broker_url, backend=result_backend)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=os.getenv("APP_TIMEZONE", "Europe/Brussels"),
    beat_schedule={
        "renew-graph-subscriptions": {
            "task": "api.tasks.renew_graph_subscriptions_task",
            "schedule": int(os.getenv("GRAPH_SUBSCRIPTION_RENEW_INTERVAL_SECONDS", "3600")),
        },
        "prune-retention": {
            "task": "api.tasks.prune_retention_task",
            "schedule": int(os.getenv("RETENTION_PRUNE_INTERVAL_SECONDS", "86400")),
        },
    },
)
celery_app.autodiscover_tasks(["api"])
