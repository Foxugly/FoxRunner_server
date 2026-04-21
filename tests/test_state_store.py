import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from state.store import HistoryStore, LastRunStore, NextExecutionStore


class StateStoreTests(unittest.TestCase):
    def test_history_store_appends_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            store = HistoryStore(path)
            store.append(
                slot_key="2026-04-07|weekday_evening|17:30-18:00",
                slot_id="weekday_evening",
                scenario_id="solidaris_pointer",
                execution_id="exec1",
                executed_at=datetime(2026, 4, 7, 17, 45, 0),
                status="success",
                step="notify",
                message="ok",
            )
            content = path.read_text(encoding="utf-8")
            self.assertIn('"slot_id": "weekday_evening"', content)
            self.assertIn('"scenario_id": "solidaris_pointer"', content)
            self.assertIn('"execution_id": "exec1"', content)

    def test_history_store_can_filter_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            store = HistoryStore(path)
            store.append(
                slot_key="2026-04-07|weekday_evening|17:30-18:00",
                slot_id="weekday_evening",
                scenario_id="solidaris_pointer",
                execution_id="exec1",
                executed_at=datetime(2026, 4, 7, 17, 45, 0),
                status="success",
                step="notify",
                message="ok",
            )
            store.append(
                slot_key="2026-04-07|weekday_morning|08:00-08:15",
                slot_id="weekday_morning",
                scenario_id="other_scenario",
                execution_id="exec2",
                executed_at=datetime(2026, 4, 7, 8, 5, 0),
                status="failed",
                step="click",
                message="ko",
            )
            rows = store.read(status="failed", scenario_id="other_scenario", limit=1)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["slot_id"], "weekday_morning")
            rows = store.read(execution_id="exec1")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["execution_id"], "exec1")

    def test_history_store_can_prune_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            store = HistoryStore(path)
            store.append(
                slot_key="2026-03-01|slot|08:00-08:15",
                slot_id="slot",
                scenario_id="scenario",
                execution_id="old",
                executed_at=datetime(2026, 3, 1, 8, 0, 0),
                status="success",
                step="notify",
                message="ok",
            )
            store.append(
                slot_key="2099-03-01|slot|08:00-08:15",
                slot_id="slot",
                scenario_id="scenario",
                execution_id="new",
                executed_at=datetime(2099, 3, 1, 8, 0, 0),
                status="success",
                step="notify",
                message="ok",
            )
            removed = store.prune(older_than_days=30)
            self.assertEqual(removed, 1)
            rows = store.read()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["execution_id"], "new")

    def test_next_execution_store_writes_expected_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "next.json"
            store = NextExecutionStore(path)
            store.save(
                "2026-04-07|weekday_evening|17:30-18:00",
                datetime(2026, 4, 7, 17, 55, 0),
                status="planned",
                slot_id="weekday_evening",
                scenario_id="solidaris_pointer",
                execution_id="exec1",
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "planned")
            self.assertEqual(payload["slot_id"], "weekday_evening")
            self.assertEqual(payload["execution_id"], "exec1")

    def test_last_run_store_writes_expected_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "last_run.json"
            store = LastRunStore(path)
            store.save(
                slot_key="2026-04-07|weekday_evening|17:30-18:00",
                slot_id="weekday_evening",
                scenario_id="solidaris_pointer",
                execution_id="exec1",
                executed_at=datetime(2026, 4, 7, 17, 56, 0),
                status="success",
                step="notify",
                message="ok",
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["scenario_id"], "solidaris_pointer")
            self.assertEqual(payload["execution_id"], "exec1")
