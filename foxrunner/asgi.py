"""ASGI entry point (future use; WSGI + sync views is the current target)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "foxrunner.settings")

from django.core.asgi import get_asgi_application  # noqa: E402

application = get_asgi_application()
