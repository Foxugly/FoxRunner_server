"""WSGI entry point (for gunicorn in production)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "foxrunner.settings")

from django.core.wsgi import get_wsgi_application  # noqa: E402 - must follow setdefault

application = get_wsgi_application()
