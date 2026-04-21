import tempfile
import unittest
from pathlib import Path

from scenarios.loader import load_network_config_from_scenarios, load_scenario_data, load_scenarios, load_slots


class ConfigLoadingTests(unittest.TestCase):
    def test_load_slots_and_scenarios(self):
        slots = load_slots(Path("config/slots.json"))
        scenarios = load_scenarios(Path("config/scenarios.json"))
        self.assertGreaterEqual(len(slots), 1)
        self.assertIn("solidaris_pointer", scenarios)
        self.assertTrue(scenarios["solidaris_pointer"].requires_enterprise_network)

    def test_load_network_from_scenarios(self):
        config = load_network_config_from_scenarios(Path("config/scenarios.json"))
        self.assertIn("sm-ms.lan", config.office_dns_suffixes)

    def test_load_named_data_from_scenarios(self):
        data = load_scenario_data(Path("config/scenarios.json"))
        self.assertIn("default", data.pushovers)
        self.assertIn("office", data.networks)
        self.assertEqual(data.default_pushover_key, "default")
        self.assertEqual(data.default_network_key, "office")

    def test_invalid_scenario_missing_steps_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text('{"schema_version":1,"data":{},"scenarios":{"x":{"description":"bad"}}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_scenarios(path)

    def test_invalid_schema_version_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text('{"schema_version":99,"data":{},"scenarios":{}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_scenarios(path)
