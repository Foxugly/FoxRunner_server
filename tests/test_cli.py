import json
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.main import main


class CliTests(unittest.TestCase):
    def test_export_plan_writes_json_file(self):
        with TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "plan.json"
            fake_service = type("S", (), {"describe_plan": lambda self: {"slot_id": "slot1"}})()
            with patch("app.main.build_runtime_services", return_value=fake_service):
                with patch("app.main.load_config", return_value=object()):
                    with patch("sys.argv", ["main.py", "--export-plan", str(output_path)]):
                        self.assertEqual(main(), 0)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["slot_id"], "slot1")

    def test_history_accepts_execution_id_filter(self):
        fake_service = type("S", (), {"read_history": lambda self, **kwargs: [{"execution_id": kwargs["execution_id"]}]})()
        with patch("app.main.build_runtime_services", return_value=fake_service):
            with patch("app.main.load_config", return_value=object()):
                with patch("sys.argv", ["main.py", "--history", "--history-execution-id", "exec42"]):
                    with patch("sys.stdout", new_callable=StringIO) as stdout:
                        self.assertEqual(main(), 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload[0]["execution_id"], "exec42")
