from __future__ import annotations

from app.config import NetworkConfig
from network.vpn import (
    DetectionResult,
    NetworkDetectionConfig,
    NetworkLocation,
    detect_network_location,
)


def build_network_detection_config(config: NetworkConfig) -> NetworkDetectionConfig:
    return NetworkDetectionConfig(
        office_ipv4_networks=config.office_ipv4_networks,
        office_gateway_networks=config.office_gateway_networks,
        office_dns_suffixes=config.office_dns_suffixes,
        vpn_interface_keywords=config.vpn_interface_keywords,
        vpn_process_names=config.vpn_process_names,
        internal_test_hosts=config.internal_test_hosts,
        internal_test_ports=config.internal_test_ports,
        tcp_timeout_seconds=config.tcp_timeout_seconds,
        home_like_networks=config.home_like_networks,
        allow_private_non_home_heuristic_for_vpn=config.allow_private_non_home_heuristic_for_vpn,
    )


def detect_enterprise_network(config: NetworkConfig) -> DetectionResult:
    return detect_network_location(build_network_detection_config(config))


def is_enterprise_location(result: DetectionResult) -> bool:
    return result.location in (NetworkLocation.OFFICE, NetworkLocation.OFFICE_VIA_VPN)
