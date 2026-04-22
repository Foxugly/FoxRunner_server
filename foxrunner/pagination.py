from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.db.models import QuerySet
from ninja import Schema


class PageQuery(Schema):
    limit: int = 100
    offset: int = 0


def page_response(items: list[Any], *, total: int, limit: int, offset: int) -> dict[str, Any]:
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def paginate[T](qs: QuerySet[T], *, page: PageQuery, serialize: Callable[[T], Any]) -> dict[str, Any]:
    limit = max(1, min(page.limit, 500))  # clamp upper bound for safety
    offset = max(0, page.offset)
    total = qs.count()
    items = [serialize(obj) for obj in qs[offset : offset + limit]]
    return page_response(items, total=total, limit=limit, offset=offset)
