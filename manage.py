#!/usr/bin/env python
"""Django management entry point.

Ensures the repository root is on ``sys.path`` so non-web modules
(``app``, ``scheduler``, ``scenarios``, ``operations``, ``network``,
``state``, ``cli``) import cleanly alongside the Django apps.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "foxrunner.settings")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError("Django is not installed. Run 'pip install -r requirements.txt' first.") from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
