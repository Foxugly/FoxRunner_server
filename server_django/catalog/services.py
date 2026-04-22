"""Catalog domain services.

Hosts the logic currently living in ``api/catalog.py``. The per-scenario
threading lock around ``save_scenario_definition`` ensures concurrent API
writes for the same scenario serialize safely (the JSON-file sync added
in Phase 4.2 needs single-writer semantics).
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

from django.db import transaction

from catalog.models import Scenario

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)


def _lock_for(scenario_id: str) -> threading.Lock:
    """Return a per-scenario lock so concurrent writes for the same scenario serialize.

    A separate guard lock protects the dict from concurrent insertion races.
    """
    with _LOCKS_GUARD:
        return _LOCKS[scenario_id]


@transaction.atomic
def save_scenario_definition(
    scenario: Scenario,
    definition: dict[str, Any],
    *,
    description: str | None = None,
) -> Scenario:
    """Persist a new definition for a scenario. JSON-file sync added in Task 4.2."""
    with _lock_for(scenario.scenario_id):
        scenario.definition = definition
        if description is not None:
            scenario.description = description
        scenario.save()
        return scenario
