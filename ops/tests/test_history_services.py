"""Unit tests for the Phase 4.8 execution-history services.

Covers ``ops.services.import_history_jsonl`` (the legacy JSONL sync),
``list_history`` (queryset filter helper), and ``serialize_history``
(the output payload shape). The endpoint integration is tested in
``catalog/tests/test_history_api.py``.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from datetime import UTC, datetime

from django.test import TestCase

from ops.models import ExecutionHistory
from ops.services import import_history_jsonl, list_history, serialize_history


def _write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _row(
    *,
    slot_id: str = "slot-1",
    scenario_id: str = "sc-1",
    execution_id: str | None = "exec-1",
    executed_at: str = "2026-04-22T10:00:00Z",
    status: str = "ok",
    step: str = "",
    message: str = "",
) -> dict:
    return {
        "slot_id": slot_id,
        "slot_key": f"{slot_id}-key",
        "scenario_id": scenario_id,
        "execution_id": execution_id,
        "executed_at": executed_at,
        "status": status,
        "step": step,
        "message": message,
    }


class ImportHistoryJsonlTest(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.path = pathlib.Path(self.tmpdir.name) / "history.jsonl"

    def test_jsonl_import_skips_missing_file(self):
        # Non-existent path should be a no-op (returns 0, no crash).
        missing = pathlib.Path(self.tmpdir.name) / "does-not-exist.jsonl"
        self.assertEqual(import_history_jsonl(missing), 0)
        self.assertEqual(ExecutionHistory.objects.count(), 0)

    def test_jsonl_import_empty_file(self):
        self.path.write_text("", encoding="utf-8")
        self.assertEqual(import_history_jsonl(self.path), 0)
        self.assertEqual(ExecutionHistory.objects.count(), 0)

    def test_jsonl_import_blank_lines_skipped(self):
        with self.path.open("w", encoding="utf-8") as handle:
            handle.write("\n")
            handle.write(json.dumps(_row()) + "\n")
            handle.write("   \n")
        self.assertEqual(import_history_jsonl(self.path), 1)
        self.assertEqual(ExecutionHistory.objects.count(), 1)

    def test_jsonl_import_idempotent(self):
        # Same file imported twice -- update_or_create on
        # (execution_id, slot_id, scenario_id) keeps the row count stable.
        rows = [
            _row(slot_id="s1", scenario_id="sc-a", execution_id="e1"),
            _row(slot_id="s2", scenario_id="sc-b", execution_id="e2"),
        ]
        _write_jsonl(self.path, rows)
        self.assertEqual(import_history_jsonl(self.path), 2)
        self.assertEqual(ExecutionHistory.objects.count(), 2)
        # Re-import: same identity triple -> in-place update, no new rows.
        self.assertEqual(import_history_jsonl(self.path), 2)
        self.assertEqual(ExecutionHistory.objects.count(), 2)

    def test_jsonl_import_updates_in_place(self):
        # Re-importing with a changed status value updates the existing row.
        _write_jsonl(self.path, [_row(status="ok", message="first")])
        import_history_jsonl(self.path)
        _write_jsonl(self.path, [_row(status="error", message="retry")])
        import_history_jsonl(self.path)
        self.assertEqual(ExecutionHistory.objects.count(), 1)
        record = ExecutionHistory.objects.get()
        self.assertEqual(record.status, "error")
        self.assertEqual(record.message, "retry")

    def test_jsonl_import_skips_rows_without_required_ids(self):
        # The unique constraint key requires slot_id + scenario_id; rows
        # missing either are silently skipped (mirrors FastAPI).
        _write_jsonl(
            self.path,
            [
                {"slot_id": "", "scenario_id": "sc-x", "executed_at": "2026-04-22T10:00:00Z", "status": "ok"},
                {"slot_id": "s1", "scenario_id": "", "executed_at": "2026-04-22T10:00:00Z", "status": "ok"},
                _row(slot_id="s-good", scenario_id="sc-good"),
            ],
        )
        self.assertEqual(import_history_jsonl(self.path), 1)
        self.assertEqual(ExecutionHistory.objects.count(), 1)
        self.assertEqual(ExecutionHistory.objects.get().slot_id, "s-good")

    def test_jsonl_import_handles_missing_execution_id(self):
        # ``execution_id`` is nullable -- rows without it should still
        # import (the unique constraint allows NULL).
        _write_jsonl(self.path, [_row(execution_id=None)])
        self.assertEqual(import_history_jsonl(self.path), 1)
        record = ExecutionHistory.objects.get()
        self.assertIsNone(record.execution_id)


class ListHistoryTest(TestCase):
    def setUp(self):
        # Three rows, descending executed_at order so the queryset already
        # matches the post-order shape (newest first).
        self.row_a = ExecutionHistory.objects.create(
            slot_key="k-a",
            slot_id="s-a",
            scenario_id="sc-1",
            execution_id="e-a",
            executed_at=datetime(2026, 4, 20, 10, 0, tzinfo=UTC),
            status="ok",
        )
        self.row_b = ExecutionHistory.objects.create(
            slot_key="k-b",
            slot_id="s-b",
            scenario_id="sc-2",
            execution_id="e-b",
            executed_at=datetime(2026, 4, 21, 10, 0, tzinfo=UTC),
            status="error",
        )
        self.row_c = ExecutionHistory.objects.create(
            slot_key="k-c",
            slot_id="s-a",
            scenario_id="sc-1",
            execution_id="e-c",
            executed_at=datetime(2026, 4, 22, 10, 0, tzinfo=UTC),
            status="ok",
        )

    def test_list_history_no_filter_orders_desc(self):
        rows = list(list_history())
        self.assertEqual([r.execution_id for r in rows], ["e-c", "e-b", "e-a"])

    def test_list_history_filter_by_status(self):
        rows = list(list_history(status="ok"))
        self.assertEqual({r.execution_id for r in rows}, {"e-a", "e-c"})

    def test_list_history_filter_by_slot_id(self):
        rows = list(list_history(slot_id="s-a"))
        self.assertEqual({r.execution_id for r in rows}, {"e-a", "e-c"})

    def test_list_history_filter_by_scenario_id(self):
        rows = list(list_history(scenario_id="sc-2"))
        self.assertEqual({r.execution_id for r in rows}, {"e-b"})

    def test_list_history_filter_by_execution_id(self):
        rows = list(list_history(execution_id="e-b"))
        self.assertEqual({r.execution_id for r in rows}, {"e-b"})

    def test_list_history_filter_by_scenario_ids_set(self):
        rows = list(list_history(scenario_ids={"sc-1"}))
        self.assertEqual({r.execution_id for r in rows}, {"e-a", "e-c"})

    def test_list_history_empty_scenario_ids_set_returns_none(self):
        # Mirrors the FastAPI early-return: an empty allowed-set must
        # short-circuit to no rows.
        rows = list(list_history(scenario_ids=set()))
        self.assertEqual(rows, [])

    def test_list_history_scenario_id_takes_precedence_over_set(self):
        # When both ``scenario_id`` and ``scenario_ids`` are provided,
        # the single-row filter wins (mirrors FastAPI 47-52).
        rows = list(list_history(scenario_id="sc-2", scenario_ids={"sc-1"}))
        self.assertEqual({r.execution_id for r in rows}, {"e-b"})


class SerializeHistoryTest(TestCase):
    def test_serialize_history_z_suffix_and_payload_shape(self):
        record = ExecutionHistory.objects.create(
            slot_key="k",
            slot_id="s1",
            scenario_id="sc1",
            execution_id="e1",
            executed_at=datetime(2026, 4, 22, 10, 30, tzinfo=UTC),
            status="ok",
            step="login",
            message="all good",
        )
        payload = serialize_history(record)
        self.assertEqual(payload["slot_key"], "k")
        self.assertEqual(payload["slot_id"], "s1")
        self.assertEqual(payload["scenario_id"], "sc1")
        self.assertEqual(payload["execution_id"], "e1")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["step"], "login")
        self.assertEqual(payload["message"], "all good")
        # The ISO 8601 string MUST end in "Z" (not "+00:00").
        self.assertTrue(payload["executed_at"].endswith("Z"))
        self.assertNotIn("+00:00", payload["executed_at"])
        self.assertIsNone(payload["updated_at"])
