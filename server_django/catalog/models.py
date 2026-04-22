"""Catalog models — Scenario, Slot, ScenarioShare.

Fleshed out in migration phases 2 and 4. Each field mirrors the SQLAlchemy
counterpart in ``api/models.py``. Indexes declared by past Alembic
revisions (``20260421_0007``, ``20260421_0009``, ``20260421_0011``) must be
reproduced here so ``makemigrations`` stays drift-free.
"""

from __future__ import annotations

# Models intentionally empty at scaffold time — see the handoff brief for
# the full mapping table. Populate during phase 2.
