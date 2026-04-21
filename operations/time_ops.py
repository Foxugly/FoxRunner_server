from __future__ import annotations

import random
import time

from .registry import OperationContext


def handle_sleep(context: OperationContext, payload: dict) -> None:
    if context.dry_run:
        return
    time.sleep(float(payload["seconds"]))


def handle_sleep_random(context: OperationContext, payload: dict) -> None:
    if context.dry_run:
        return
    time.sleep(random.uniform(float(payload["min_seconds"]), float(payload["max_seconds"])))
