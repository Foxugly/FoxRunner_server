"""Catalog domain services.

Hosts the logic currently living in ``api/catalog.py`` and
``api/catalog_queries.py``. The per-file asyncio lock around
``save_scenario_definition`` must be preserved (as a ``threading.Lock``)
so concurrent scenario updates never drop the JSON-sync write.
"""

from __future__ import annotations
