from __future__ import annotations

import json
import runpy
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cli import history_cli


class CliAndEntrypointTests(unittest.TestCase):
    def test_history_cli_prints_filtered_rows(self):
        args = SimpleNamespace(limit=2, status="success", slot_id="slot1", scenario_id="scenario", execution_id="exec")
        config = SimpleNamespace(runtime=SimpleNamespace(history_file="history.jsonl"))
        store = MagicMock()
        store.read.return_value = [{"execution_id": "exec", "status": "success"}]

        with (
            patch("cli.history_cli.parse_args", return_value=args),
            patch("cli.history_cli.load_config", return_value=config),
            patch("cli.history_cli.HistoryStore", return_value=store),
            patch("builtins.print") as printed,
        ):
            self.assertEqual(history_cli.main(), 0)

        store.read.assert_called_once_with(limit=2, status="success", slot_id="slot1", scenario_id="scenario", execution_id="exec")
        self.assertEqual(json.loads(printed.call_args.args[0])["execution_id"], "exec")

    def test_parse_args_and_module_entrypoints(self):
        with patch("sys.argv", ["history", "--limit", "3", "--status", "failed"]):
            args = history_cli.parse_args()
        self.assertEqual(args.limit, 3)
        self.assertEqual(args.status, "failed")

        with patch("cli.history_cli.main", return_value=7):
            with self.assertRaises(SystemExit) as exc:
                runpy.run_module("cli.__main__", run_name="__main__")
            self.assertEqual(exc.exception.code, 7)

        with patch("app.main.main", return_value=5):
            with self.assertRaises(SystemExit) as exc:
                runpy.run_module("app.__main__", run_name="__main__")
            self.assertEqual(exc.exception.code, 5)


if __name__ == "__main__":
    unittest.main()
