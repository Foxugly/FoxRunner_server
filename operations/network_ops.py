from __future__ import annotations

from .registry import OperationContext


def handle_require_enterprise_network(context: OperationContext, payload: dict) -> None:
    network_key = context.resolve_ref(payload, "network", legacy_key="network_key")

    if network_key is not None and context.network_check_by_key is not None:
        if not context.network_check_by_key(network_key):
            raise RuntimeError("Reseau entreprise/VPN non disponible pendant le scenario.")
        return

    if context.network_check is not None and not context.network_check():
        raise RuntimeError("Reseau entreprise/VPN non disponible pendant le scenario.")
