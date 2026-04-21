from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scenarios.loader import (
    ExtractTarget,
    StepReference,
    build_scenarios_from_map,
    build_slots_from_items,
    load_network_config_from_scenarios,
    load_pushover_from_scenarios,
    load_scenario_data,
    load_scenarios,
    load_slots,
    validate_scenarios_document,
    validate_slots_document,
)


def _network_payload() -> dict[str, object]:
    return {
        "office_ipv4_networks": ["10.0.0.0/8"],
        "office_gateway_networks": ["10.0.0.0/8"],
        "office_dns_suffixes": ["corp.example"],
        "vpn_interface_keywords": ["vpn"],
        "vpn_process_names": ["vpn.exe"],
        "internal_test_hosts": ["intranet"],
        "internal_test_ports": [443],
        "tcp_timeout_seconds": 0.5,
        "home_like_networks": ["192.168.0.0/16"],
        "allow_private_non_home_heuristic_for_vpn": True,
    }


class LoaderEdgeTests(unittest.TestCase):
    def test_load_slots_and_scenarios_build_nested_steps_and_defaults(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            slots_file = base / "slots.json"
            slots_file.write_text(
                json.dumps({"slots": [{"id": "slot1", "days": [0], "start": "08:00", "end": "09:30", "scenario": "scenario"}]}),
                encoding="utf-8",
            )
            scenarios_file = base / "scenarios.json"
            scenarios_file.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "data": {
                            "pushovers": {"ops": {"token": "token", "user_key": "user", "sound": "magic", "timeout_seconds": 5}},
                            "networks": {"office": _network_payload()},
                            "default_pushover": "ops",
                            "default_network": "office",
                        },
                        "scenarios": {
                            "scenario": {
                                "description": "Demo",
                                "before_steps": [{"type": "require_enterprise_network"}],
                                "steps": [
                                    {
                                        "type": "group",
                                        "steps": [
                                            {"type": "notify", "message": "hello", "ref": {"pushover": "ops", "network": "office"}},
                                            {"type": "extract_attribute_to_context", "key": "href", "by": "id", "locator": "link", "attribute": "href"},
                                        ],
                                    }
                                ],
                                "on_success": [{"type": "repeat", "times": 2, "steps": [{"type": "sleep", "seconds": 1}]}],
                                "on_failure": [{"type": "try", "try_steps": [{"type": "sleep", "seconds": 1}], "finally_steps": [{"type": "notify", "message": "done"}]}],
                                "finally_steps": [{"type": "extract_text_to_context", "key": "title", "by": "css", "locator": "h1"}],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            slots = load_slots(slots_file)
            scenarios = load_scenarios(scenarios_file)
            data = load_scenario_data(scenarios_file)
            pushover = load_pushover_from_scenarios(scenarios_file)
            network = load_network_config_from_scenarios(scenarios_file)

        scenario = scenarios["scenario"]
        self.assertEqual(slots[0].start_hour, 8)
        self.assertTrue(scenario.requires_enterprise_network)
        self.assertIsInstance(scenario.steps[0].payload["steps"][0].payload["ref"], StepReference)
        self.assertIsInstance(scenario.steps[0].payload["steps"][1].payload["target"], ExtractTarget)
        self.assertEqual(data.default_network_key, "office")
        self.assertEqual(pushover.user_key, "user")
        self.assertEqual(network.internal_test_ports, (443,))

    def test_invalid_slots_and_scenario_documents_raise_clear_errors(self):
        with self.assertRaises(ValueError):
            build_slots_from_items([{"id": "bad", "days": ["monday"], "start": "08:00", "end": "09:00", "scenario": "s"}])
        with self.assertRaises(ValueError):
            build_scenarios_from_map({"bad": "not-a-dict"})
        with self.assertRaises(ValueError):
            build_scenarios_from_map({"bad": {"steps": [{"type": "unknown"}]}})
        with self.assertRaises(ValueError):
            build_scenarios_from_map({"bad": {"steps": [{"type": "sleep", "seconds": 1, "retry": -1}]}})
        with self.assertRaises(ValueError):
            build_scenarios_from_map({"bad": {"steps": [{"type": "sleep", "seconds": 1, "retry_delay_seconds": -1}]}})
        with self.assertRaises(ValueError):
            build_scenarios_from_map({"bad": {"steps": [{"type": "sleep", "seconds": 1, "retry_backoff_seconds": 0.5}]}})
        with self.assertRaises(ValueError):
            build_scenarios_from_map({"bad": {"steps": [{"type": "sleep", "seconds": 1, "when": 123}]}})
        with self.assertRaises(ValueError):
            build_scenarios_from_map({"bad": {"steps": [{"type": "repeat", "times": 0, "steps": [{"type": "sleep", "seconds": 1}]}]}})
        with self.assertRaises(ValueError):
            validate_slots_document({"slots": "bad"}, "slots.json")
        with self.assertRaises(ValueError):
            validate_scenarios_document({"schema_version": 999, "data": {}, "scenarios": {}}, "scenarios.json")
        with self.assertRaises(ValueError):
            validate_scenarios_document({"schema_version": 1, "data": [], "scenarios": {}}, "scenarios.json")
        with self.assertRaises(ValueError):
            validate_scenarios_document({"schema_version": 1, "data": {}, "scenarios": []}, "scenarios.json")
        with self.assertRaises(ValueError):
            validate_scenarios_document({"schema_version": 1, "data": {}, "scenarios": {"s": []}}, "scenarios.json")
        with self.assertRaises(ValueError):
            validate_scenarios_document({"schema_version": 1, "data": {}, "scenarios": {"s": {"steps": "bad"}}}, "scenarios.json")

    def test_data_validation_errors_and_empty_defaults(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            empty_file = base / "empty.json"
            empty_file.write_text(json.dumps({"schema_version": 1, "data": {}, "scenarios": {}}), encoding="utf-8")
            bad_root = base / "bad-root.json"
            bad_root.write_text("[]", encoding="utf-8")
            bad_data = base / "bad-data.json"
            bad_data.write_text(json.dumps({"schema_version": 1, "data": {"pushovers": [], "networks": {}}, "scenarios": {}}), encoding="utf-8")
            bad_network = base / "bad-network.json"
            bad_network.write_text(json.dumps({"schema_version": 1, "data": {"networks": {"office": {"office_ipv4_networks": "bad"}}}, "scenarios": {}}), encoding="utf-8")
            bad_default = base / "bad-default.json"
            bad_default.write_text(json.dumps({"schema_version": 1, "data": {"pushovers": {}, "default_pushover": 123}, "scenarios": {}}), encoding="utf-8")
            missing_default = base / "missing-default.json"
            missing_default.write_text(json.dumps({"schema_version": 1, "data": {"networks": {}, "default_network": "missing"}, "scenarios": {}}), encoding="utf-8")

            self.assertIsNone(load_pushover_from_scenarios(empty_file))
            with self.assertRaises(ValueError):
                load_network_config_from_scenarios(empty_file)
            with self.assertRaises(ValueError):
                load_scenario_data(bad_root)
            with self.assertRaises(ValueError):
                load_scenario_data(bad_data)
            with self.assertRaises(ValueError):
                load_scenario_data(bad_network)
            with self.assertRaises(ValueError):
                load_scenario_data(bad_default)
            with self.assertRaises(ValueError):
                load_scenario_data(missing_default)


if __name__ == "__main__":
    unittest.main()
