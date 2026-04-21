from __future__ import annotations

import ipaddress
import json
import platform
import re
import socket
import subprocess
from dataclasses import asdict, dataclass
from enum import StrEnum


class NetworkLocation(StrEnum):
    OFFICE = "office"
    OFFICE_VIA_VPN = "office_via_vpn"
    OTHER = "other"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class InterfaceInfo:
    name: str
    ipv4: tuple[str, ...] = ()
    ipv6: tuple[str, ...] = ()
    gateways: tuple[str, ...] = ()
    dns_suffix: str | None = None
    is_up: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DetectionEvidence:
    has_office_ip: bool = False
    has_office_gateway: bool = False
    has_office_dns_suffix: bool = False
    vpn_adapter_detected: bool = False
    vpn_process_detected: bool = False
    internal_host_reachable: bool = False
    has_non_home_private_ip: bool = False
    has_non_home_gateway: bool = False
    matching_office_interfaces: tuple[str, ...] = ()
    matching_vpn_interfaces: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DetectionResult:
    location: NetworkLocation
    interfaces: tuple[InterfaceInfo, ...]
    evidence: DetectionEvidence

    def to_dict(self) -> dict:
        data = asdict(self)
        data["location"] = self.location.value
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@dataclass(frozen=True)
class NetworkDetectionConfig:
    office_ipv4_networks: tuple[str, ...] = ()
    office_gateway_networks: tuple[str, ...] = ()
    office_dns_suffixes: tuple[str, ...] = ()
    vpn_interface_keywords: tuple[str, ...] = ()
    vpn_process_names: tuple[str, ...] = ()
    internal_test_hosts: tuple[str, ...] = ()
    internal_test_ports: tuple[int, ...] = (443, 80, 445)
    tcp_timeout_seconds: float = 1.0
    home_like_networks: tuple[str, ...] = ("192.168.0.0/16", "192.168.1.0/16")
    allow_private_non_home_heuristic_for_vpn: bool = True


class NetworkDetector:
    def __init__(self, config: NetworkDetectionConfig):
        self.config = config
        self._office_networks = tuple(ipaddress.ip_network(net, strict=False) for net in config.office_ipv4_networks)
        self._office_gateway_networks = tuple(ipaddress.ip_network(net, strict=False) for net in config.office_gateway_networks)
        self._home_like_networks = tuple(ipaddress.ip_network(net, strict=False) for net in config.home_like_networks)

    def detect(self) -> DetectionResult:
        interfaces = self._collect_interfaces()
        office_interface_names: list[str] = []
        vpn_interface_names: list[str] = []
        notes: list[str] = []

        has_office_ip = False
        has_office_gateway = False
        has_office_dns_suffix = False
        vpn_adapter_detected = False
        vpn_process_detected = self._vpn_process_detected()

        for iface in interfaces:
            if self._is_vpn_interface_name(iface.name):
                vpn_adapter_detected = True
                vpn_interface_names.append(iface.name)

            if iface.is_up and iface.dns_suffix and self._is_office_dns_suffix(iface.dns_suffix):
                has_office_dns_suffix = True
                if iface.name not in office_interface_names:
                    office_interface_names.append(iface.name)

            if not iface.is_up:
                continue

            for ip in iface.ipv4:
                if self._ip_in_office_networks(ip):
                    has_office_ip = True
                    if iface.name not in office_interface_names:
                        office_interface_names.append(iface.name)

            for gateway in iface.gateways:
                if self._gateway_in_office_networks(gateway):
                    has_office_gateway = True
                    if iface.name not in office_interface_names:
                        office_interface_names.append(iface.name)

        internal_host_reachable = False
        if self.config.internal_test_hosts:
            internal_host_reachable = self._any_internal_host_reachable()
            if internal_host_reachable:
                notes.append("Au moins un hôte interne est joignable.")

        active_interfaces = [iface for iface in interfaces if iface.is_up]
        active_ipv4s = [ip for iface in active_interfaces for ip in iface.ipv4]
        active_gateways = [gateway for iface in active_interfaces for gateway in iface.gateways]

        has_non_home_private_ip = any(self._is_private_ipv4(ip) and not self._looks_like_home_ip(ip) for ip in active_ipv4s)
        has_non_home_gateway = any(self._is_private_ipv4(gateway) and not self._looks_like_home_ip(gateway) for gateway in active_gateways)

        location = self._classify_location(
            has_office_ip=has_office_ip,
            has_office_gateway=has_office_gateway,
            has_office_dns_suffix=has_office_dns_suffix,
            vpn_adapter_detected=vpn_adapter_detected,
            vpn_process_detected=vpn_process_detected,
            internal_host_reachable=internal_host_reachable,
            has_non_home_private_ip=has_non_home_private_ip,
            has_non_home_gateway=has_non_home_gateway,
            notes=notes,
        )

        evidence = DetectionEvidence(
            has_office_ip=has_office_ip,
            has_office_gateway=has_office_gateway,
            has_office_dns_suffix=has_office_dns_suffix,
            vpn_adapter_detected=vpn_adapter_detected,
            vpn_process_detected=vpn_process_detected,
            internal_host_reachable=internal_host_reachable,
            has_non_home_private_ip=has_non_home_private_ip,
            has_non_home_gateway=has_non_home_gateway,
            matching_office_interfaces=tuple(office_interface_names),
            matching_vpn_interfaces=tuple(vpn_interface_names),
            notes=tuple(notes),
        )

        return DetectionResult(location=location, interfaces=interfaces, evidence=evidence)

    def _classify_location(
        self,
        *,
        has_office_ip: bool,
        has_office_gateway: bool,
        has_office_dns_suffix: bool,
        vpn_adapter_detected: bool,
        vpn_process_detected: bool,
        internal_host_reachable: bool,
        has_non_home_private_ip: bool,
        has_non_home_gateway: bool,
        notes: list[str],
    ) -> NetworkLocation:
        vpn_detected = vpn_adapter_detected or vpn_process_detected

        if has_office_ip or has_office_gateway:
            notes.append("Présence d'une IP ou d'une passerelle appartenant au réseau du bureau.")
            return NetworkLocation.OFFICE

        if has_office_dns_suffix and not vpn_detected:
            notes.append("Suffixe DNS interne détecté sans indice VPN.")
            return NetworkLocation.OFFICE

        if vpn_detected and (has_office_dns_suffix or internal_host_reachable or has_office_ip or has_office_gateway):
            notes.append("VPN détecté avec indice d'accès au réseau d'entreprise.")
            return NetworkLocation.OFFICE_VIA_VPN

        if vpn_detected and self.config.allow_private_non_home_heuristic_for_vpn and (has_non_home_private_ip or has_non_home_gateway):
            notes.append("VPN détecté avec IP ou passerelle privée non domestique.")
            return NetworkLocation.OFFICE_VIA_VPN

        notes.append("Aucun indice suffisant pour classer en réseau bureau.")
        return NetworkLocation.OTHER

    def _collect_interfaces(self) -> tuple[InterfaceInfo, ...]:
        if platform.system().lower() == "windows":
            return self._collect_interfaces_windows()
        return self._collect_interfaces_generic()

    def _collect_interfaces_windows(self) -> tuple[InterfaceInfo, ...]:
        try:
            completed = subprocess.run(
                ["ipconfig", "/all"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except Exception:
            return self._collect_interfaces_generic()

        interfaces: list[InterfaceInfo] = []
        for name, block in self._split_windows_ipconfig_blocks(completed.stdout):
            interfaces.append(
                InterfaceInfo(
                    name=name.strip(),
                    ipv4=tuple(self._extract_ipv4s(block)),
                    ipv6=tuple(self._extract_ipv6s(block)),
                    gateways=tuple(self._extract_gateways(block)),
                    dns_suffix=self._extract_dns_suffix(block),
                    is_up=not self._looks_media_disconnected(block),
                )
            )
        return tuple(interfaces)

    def _collect_interfaces_generic(self) -> tuple[InterfaceInfo, ...]:
        try:
            hostname = socket.gethostname()
            _, _, addresses = socket.gethostbyname_ex(hostname)
            ipv4 = tuple(address for address in addresses if self._is_ipv4(address))
        except Exception:
            ipv4 = ()
        return (InterfaceInfo(name="default", ipv4=ipv4, is_up=True),)

    @staticmethod
    def _split_windows_ipconfig_blocks(output: str) -> list[tuple[str, str]]:
        lines = output.splitlines()
        blocks: list[tuple[str, list[str]]] = []
        current_name: str | None = None
        current_block: list[str] = []
        adapter_header_re = re.compile(
            r"^(?:Ethernet|Wireless LAN|Unknown|PPP|Tunnel adapter)\s+adapter\s+(.+?)\s*:\s*$",
            re.IGNORECASE,
        )

        for line in lines:
            stripped = line.strip()
            match = adapter_header_re.match(stripped)
            if match:
                if current_name is not None:
                    blocks.append((current_name, current_block))
                current_name = match.group(1)
                current_block = []
                continue
            if current_name is not None:
                current_block.append(line)

        if current_name is not None:
            blocks.append((current_name, current_block))

        return [(name, "\n".join(block)) for name, block in blocks]

    @staticmethod
    def _extract_ipv4s(block: str) -> list[str]:
        addresses: list[str] = []
        for line in block.splitlines():
            if "IPv4" in line:
                addresses.extend(re.findall(r"(\d+\.\d+\.\d+\.\d+)", line))
        return addresses

    @staticmethod
    def _extract_ipv6s(block: str) -> list[str]:
        addresses: list[str] = []
        for line in block.splitlines():
            if "IPv6" not in line:
                continue
            for candidate in re.findall(r"([0-9a-fA-F:]{2,})", line):
                if ":" in candidate and "%" not in candidate:
                    addresses.append(candidate)
        return addresses

    @staticmethod
    def _extract_gateways(block: str) -> list[str]:
        gateways: list[str] = []
        lines = block.splitlines()

        for index, line in enumerate(lines):
            if "Default Gateway" not in line and "Passerelle par défaut" not in line:
                continue
            gateways.extend(re.findall(r"(\d+\.\d+\.\d+\.\d+)", line))
            offset = index + 1
            while offset < len(lines):
                next_line = lines[offset].strip()
                if not next_line:
                    break
                extra = re.findall(r"(\d+\.\d+\.\d+\.\d+)", next_line)
                if not extra:
                    break
                gateways.extend(extra)
                offset += 1

        return gateways

    @staticmethod
    def _extract_dns_suffix(block: str) -> str | None:
        for line in block.splitlines():
            if "Connection-specific DNS Suffix" in line or "Suffixe DNS propre à la connexion" in line:
                _, _, value = line.partition(":")
                value = value.strip()
                return value or None
        return None

    @staticmethod
    def _looks_media_disconnected(block: str) -> bool:
        text = block.lower()
        return "media disconnected" in text or "média déconnecté" in text

    def _ip_in_office_networks(self, ip: str) -> bool:
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(ip_obj in network for network in self._office_networks)

    def _gateway_in_office_networks(self, gateway: str) -> bool:
        try:
            gateway_obj = ipaddress.ip_address(gateway)
        except ValueError:
            return False
        if self._office_gateway_networks:
            return any(gateway_obj in network for network in self._office_gateway_networks)
        return any(gateway_obj in network for network in self._office_networks)

    def _is_office_dns_suffix(self, suffix: str) -> bool:
        suffix = suffix.strip().lower()
        return any(suffix == candidate.lower() or suffix.endswith("." + candidate.lower()) for candidate in self.config.office_dns_suffixes)

    def _is_vpn_interface_name(self, name: str) -> bool:
        lowered = name.lower()
        return any(keyword.lower() in lowered for keyword in self.config.vpn_interface_keywords)

    def _vpn_process_detected(self) -> bool:
        if platform.system().lower() != "windows":
            return False
        try:
            completed = subprocess.run(
                ["tasklist"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except Exception:
            return False
        output = completed.stdout.lower()
        return any(process.lower() in output for process in self.config.vpn_process_names)

    def _any_internal_host_reachable(self) -> bool:
        return any(self._is_host_reachable(host) for host in self.config.internal_test_hosts)

    def _is_host_reachable(self, host: str) -> bool:
        for port in self.config.internal_test_ports:
            try:
                with socket.create_connection((host, port), timeout=self.config.tcp_timeout_seconds):
                    return True
            except OSError:
                continue
        return False

    @staticmethod
    def _is_ipv4(value: str) -> bool:
        try:
            return isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address)
        except ValueError:
            return False

    @staticmethod
    def _is_private_ipv4(ip: str) -> bool:
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return isinstance(parsed, ipaddress.IPv4Address) and parsed.is_private

    def _looks_like_home_ip(self, ip: str) -> bool:
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(parsed in network for network in self._home_like_networks)


def detect_network_location(config: NetworkDetectionConfig) -> DetectionResult:
    return NetworkDetector(config).detect()


def pretty_print_result(result: DetectionResult) -> None:
    print(f"Location détectée : {result.location.value}")
    print()
    print("Indices :")
    print(f"  - IP bureau détectée           : {result.evidence.has_office_ip}")
    print(f"  - Gateway bureau détectée      : {result.evidence.has_office_gateway}")
    print(f"  - DNS suffix bureau détecté    : {result.evidence.has_office_dns_suffix}")
    print(f"  - Interface VPN détectée       : {result.evidence.vpn_adapter_detected}")
    print(f"  - Processus VPN détecté        : {result.evidence.vpn_process_detected}")
    print(f"  - Hôte interne joignable       : {result.evidence.internal_host_reachable}")
    print(f"  - IP privée non domestique     : {result.evidence.has_non_home_private_ip}")
    print(f"  - Gateway non domestique       : {result.evidence.has_non_home_gateway}")

    if result.evidence.matching_office_interfaces:
        print(f"  - Interfaces bureau            : {', '.join(result.evidence.matching_office_interfaces)}")
    if result.evidence.matching_vpn_interfaces:
        print(f"  - Interfaces VPN               : {', '.join(result.evidence.matching_vpn_interfaces)}")

    print()
    print("Interfaces :")
    for interface in result.interfaces:
        print(f"  * {interface.name}")
        print(f"      up         : {interface.is_up}")
        print(f"      ipv4       : {', '.join(interface.ipv4) if interface.ipv4 else '-'}")
        print(f"      gateways   : {', '.join(interface.gateways) if interface.gateways else '-'}")
        print(f"      dns_suffix : {interface.dns_suffix or '-'}")

    if result.evidence.notes:
        print()
        print("Notes :")
        for note in result.evidence.notes:
            print(f"  - {note}")
