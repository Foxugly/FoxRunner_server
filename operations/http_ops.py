from __future__ import annotations

import requests

from .registry import OperationContext


def handle_http_request(context: OperationContext, payload: dict) -> None:
    if context.dry_run:
        return
    response = requests.request(
        method=str(payload.get("method", "GET")).upper(),
        url=str(payload["url"]),
        headers=payload.get("headers"),
        json=payload.get("json"),
        data=payload.get("data"),
        timeout=float(payload.get("timeout", 20)),
    )
    expected_status = payload.get("expected_status")
    if expected_status is not None and response.status_code != int(expected_status):
        raise RuntimeError(f"HTTP {response.status_code} au lieu de {expected_status} pour {payload['url']}")
