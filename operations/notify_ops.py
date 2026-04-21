from __future__ import annotations

from app.notifier import Notifier

from .registry import OperationContext


def handle_notify(context: OperationContext, payload: dict) -> None:
    message = _render_message(str(payload["message"]), context.template_context)
    notifier = _resolve_notifier(context, payload)
    if notifier is None:
        return
    if context.dry_run:
        return
    notifier.send(message)


def _render_message(message: str, template_context: dict[str, str]) -> str:
    rendered = message
    for key, value in template_context.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered


def _resolve_notifier(context: OperationContext, payload: dict) -> Notifier | None:
    pushover_key = context.resolve_ref(payload, "pushover", legacy_key="pushover_key")
    if pushover_key is None:
        return context.notifier
    try:
        pushover_config = context.pushovers[pushover_key]
    except KeyError as exc:
        raise ValueError(f"Configuration Pushover inconnue: {pushover_key}") from exc
    return Notifier(pushover_config, context.logger)
