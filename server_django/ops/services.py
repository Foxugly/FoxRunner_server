"""Ops domain services.

Centralises logic currently spread across ``api/jobs.py``,
``api/history.py``, ``api/audit.py``, ``api/settings.py``,
``api/artifacts.py``, ``api/graph.py``, ``api/monitoring.py``,
``api/retention.py``. Ninja handlers stay thin and delegate here.
"""

from __future__ import annotations
