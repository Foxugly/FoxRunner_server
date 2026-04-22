"""Ninja router for catalog endpoints (scenarios, slots, shares, steps).

Populated during migration phases 3 and 4. Each endpoint replaces the
corresponding FastAPI route in ``api/routers/catalog.py`` with 1:1
behavior.
"""

from __future__ import annotations

from ninja import Router

router = Router(tags=["catalog"])
