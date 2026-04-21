from __future__ import annotations

from app.config import AppConfig
from app.logger import Logger
from app.notifier import Notifier
from network.service import detect_enterprise_network, is_enterprise_location
from network.vpn import DetectionResult
from scenarios.loader import ScenarioData


class NetworkGuard:
    def __init__(self, config: AppConfig, scenario_data: ScenarioData, logger: Logger):
        self.config = config
        self.scenario_data = scenario_data
        self.logger = logger

    def detect_default(self, context: str = "avant planification") -> DetectionResult:
        result = detect_enterprise_network(self.config.network)
        self.logger.info(f"Etat reseau detecte {context}: {result.location.value}")
        for note in result.evidence.notes:
            self.logger.info(f"Indice reseau: {note}")
        return result

    def is_default_network_available(self, context: str = "avant planification") -> bool:
        return is_enterprise_location(self.detect_default(context))

    def is_network_available_by_key(self, key: str | None) -> bool:
        if key is None:
            return self.is_default_network_available()
        try:
            network_config = self.scenario_data.networks[key]
        except KeyError as exc:
            raise ValueError(f"Configuration reseau inconnue: {key}") from exc
        return is_enterprise_location(detect_enterprise_network(network_config))

    def check_before_run(self, notifier: Notifier) -> bool:
        if self.is_default_network_available("avant execution"):
            return True
        message = "Execution annulee: machine non connectee au reseau d'entreprise ou au VPN."
        notifier.send(message)
        self.logger.warning(message)
        return False
