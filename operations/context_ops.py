from __future__ import annotations

from .notify_ops import _render_message
from .registry import OperationContext


def handle_set_context(context: OperationContext, payload: dict) -> None:
    key = str(payload["key"])
    value = payload.get("value", "")
    context.template_context[key] = str(value)


def handle_format_context(context: OperationContext, payload: dict) -> None:
    key = str(payload["key"])
    template = str(payload["template"])
    context.template_context[key] = _render_message(template, context.template_context)
