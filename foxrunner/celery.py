"""Celery application.

``django.setup()`` must run before Celery autodiscovers tasks, otherwise
the Django ORM isn't bootstrapped in worker processes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "foxrunner.settings")

import django  # noqa: E402

django.setup()

from celery import Celery  # noqa: E402

celery_app = Celery("foxrunner")
celery_app.config_from_object("django.conf:settings", namespace="CELERY")
celery_app.autodiscover_tasks()


@celery_app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
