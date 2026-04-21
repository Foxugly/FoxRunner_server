from __future__ import annotations

from typing import Any


def page_response(items: list[dict[str, Any]], *, total: int, limit: int, offset: int) -> dict[str, Any]:
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
