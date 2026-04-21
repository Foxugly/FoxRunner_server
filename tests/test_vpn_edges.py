from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from network.vpn import DetectionEvidence, DetectionResult, InterfaceInfo, NetworkDetectionConfig, NetworkDetector, NetworkLocation, detect_network_location, pretty_print_result


class VpnEdgeTests(unittest.TestCase):
    def _detector(self) -> NetworkDetector:
        return NetworkDetector(
            NetworkDetectionConfig(
                office_ipv4_networks=("10.10.0.0/16",),
                office_gateway_networks=("10.20.0.0/16",),
                office_dns_suffixes=("corp.example",),
                vpn_interface_keywords=("vpn", "globalprotect"),
                vpn_process_names=("vpnagent.exe",),
                internal_test_hosts=("intranet.local",),
                internal_test_ports=(443, 8443),
                tcp_timeout_seconds=0.01,
                home_like_networks=("192.168.0.0/16",),
                allow_private_non_home_heuristic_for_vpn=True,
            )
        )

    def test_dataclasses_to_dict_json_and_pretty_print(self):
        result = DetectionResult(
            location=NetworkLocation.OFFICE_VIA_VPN,
            interfaces=(InterfaceInfo("vpn0", ipv4=("10.10.1.2",), gateways=("10.20.1.1",), dns_suffix="corp.example"),),
            evidence=DetectionEvidence(
                has_office_ip=True,
                matching_office_interfaces=("vpn0",),
                matching_vpn_interfaces=("vpn0",),
                notes=("ok",),
            ),
        )

        self.assertEqual(result.interfaces[0].to_dict()["name"], "vpn0")
        self.assertTrue(result.evidence.to_dict()["has_office_ip"])
        self.assertEqual(json.loads(result.to_json(indent=0))["location"], "office_via_vpn")
        with patch("builtins.print") as printed:
            pretty_print_result(result)
        self.assertGreater(printed.call_count, 5)

    def test_detect_classifies_office_vpn_and_other_paths(self):
        detector = self._detector()
        office_iface = InterfaceInfo("Ethernet", ipv4=("10.10.1.2",), gateways=("10.20.1.1",), dns_suffix="corp.example")
        vpn_iface = InterfaceInfo("GlobalProtect VPN", ipv4=("172.16.1.2",), gateways=("172.16.1.1",), dns_suffix="corp.example")
        other_iface = InterfaceInfo("Wifi", ipv4=("192.168.1.20",), gateways=("192.168.1.1",))

        with patch.object(detector, "_collect_interfaces", return_value=(office_iface,)), patch.object(detector, "_vpn_process_detected", return_value=False):
            self.assertEqual(detector.detect().location, NetworkLocation.OFFICE)
        with (
            patch.object(detector, "_collect_interfaces", return_value=(vpn_iface,)),
            patch.object(detector, "_vpn_process_detected", return_value=False),
            patch.object(detector, "_any_internal_host_reachable", return_value=True),
        ):
            result = detector.detect()
            self.assertEqual(result.location, NetworkLocation.OFFICE_VIA_VPN)
            self.assertTrue(result.evidence.internal_host_reachable)
        with patch.object(detector, "_collect_interfaces", return_value=(other_iface,)), patch.object(detector, "_vpn_process_detected", return_value=False):
            self.assertEqual(detector.detect().location, NetworkLocation.OTHER)

    def test_windows_ipconfig_parsing_and_fallbacks(self):
        detector = self._detector()
        output = """
Windows IP Configuration

Ethernet adapter Corp VPN:
   Connection-specific DNS Suffix  . : corp.example
   IPv4 Address. . . . . . . . . . . : 10.10.1.2(Preferred)
   IPv6 Address. . . . . . . . . . . : 2001:db8::1
   Default Gateway . . . . . . . . . : 10.20.1.1
                                       10.20.1.2

Wireless LAN adapter Wi-Fi:
   Media State . . . . . . . . . . . : Media disconnected
"""
        blocks = detector._split_windows_ipconfig_blocks(output)

        self.assertEqual([name for name, _ in blocks], ["Corp VPN", "Wi-Fi"])
        self.assertEqual(detector._extract_ipv4s(blocks[0][1]), ["10.10.1.2"])
        self.assertEqual(detector._extract_ipv6s(blocks[0][1]), ["2001:db8::1"])
        self.assertEqual(detector._extract_gateways(blocks[0][1]), ["10.20.1.1", "10.20.1.2"])
        self.assertEqual(detector._extract_dns_suffix(blocks[0][1]), "corp.example")
        self.assertTrue(detector._looks_media_disconnected(blocks[1][1]))
        with patch("network.vpn.platform.system", return_value="Windows"), patch("network.vpn.subprocess.run", side_effect=RuntimeError):
            self.assertEqual(detector._collect_interfaces()[0].name, "default")

    def test_helpers_cover_invalid_values_process_and_socket_paths(self):
        detector = self._detector()

        self.assertFalse(detector._ip_in_office_networks("bad"))
        self.assertFalse(detector._gateway_in_office_networks("bad"))
        self.assertTrue(detector._is_office_dns_suffix("app.corp.example"))
        self.assertTrue(detector._is_vpn_interface_name("My VPN Adapter"))
        self.assertFalse(detector._is_ipv4("2001:db8::1"))
        self.assertFalse(detector._is_private_ipv4("bad"))
        self.assertFalse(detector._looks_like_home_ip("bad"))
        with patch("network.vpn.platform.system", return_value="Windows"), patch("network.vpn.subprocess.run", return_value=SimpleNamespace(stdout="vpnagent.exe")):
            self.assertTrue(detector._vpn_process_detected())
        with patch("network.vpn.platform.system", return_value="Windows"), patch("network.vpn.subprocess.run", side_effect=RuntimeError):
            self.assertFalse(detector._vpn_process_detected())
        connection = MagicMock()
        connection.__enter__.return_value = connection
        with patch("network.vpn.socket.create_connection", side_effect=[OSError, connection]):
            self.assertTrue(detector._is_host_reachable("intranet.local"))
        with patch("network.vpn.socket.gethostname", return_value="host"), patch("network.vpn.socket.gethostbyname_ex", return_value=("host", [], ["127.0.0.1", "not-ip"])):
            self.assertEqual(detector._collect_interfaces_generic()[0].ipv4, ("127.0.0.1",))
        with patch("network.vpn.NetworkDetector.detect", return_value=DetectionResult(NetworkLocation.OTHER, (), DetectionEvidence())):
            self.assertEqual(detect_network_location(detector.config).location, NetworkLocation.OTHER)


if __name__ == "__main__":
    unittest.main()
