"""Framework-agnostic hardening invariants for the CLI engine.

The FastAPI HTTP-layer hardening tests (rate limit, payload limit,
clientState validation, scenario ownership) moved to the Django side
in Phase 13:

- server_django/foxrunner/tests/test_rate_limit.py
- server_django/foxrunner/tests/test_payload_limit.py
- server_django/foxrunner/tests/test_security_headers.py
- server_django/ops/tests/test_graph_subscriptions.py
- server_django/catalog/tests/test_scenarios_api.py (ownership)

This module retains only the engine-level invariants that have no web
dependency: DST handling in :mod:`scheduler.model`, file-locking in
:class:`state.store.ProcessLock` and :class:`state.store.HistoryStore`,
``try``-block ``finally_steps`` exception propagation, and the
:func:`scenarios.runner._run_with_timeout` budget.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from scenarios.loader import ScenarioStep
from scheduler.model import find_next_pending_execution, make_dt, pick_next_execution, random_datetime_in_slot
from state.store import HistoryStore, ProcessLock

# --- DST -----------------------------------------------------------------


class DSTFoldTests(unittest.TestCase):
    def test_make_dt_picks_correct_offset_at_spring_forward(self):
        tz = ZoneInfo("Europe/Brussels")
        # 2026-03-29: spring-forward; 02:00 local does not exist, 03:00 is UTC+2.
        base = datetime(2026, 3, 29, 10, 0, tzinfo=tz)
        dt_before = make_dt(base, 1, 30)
        dt_after = make_dt(base, 4, 0)
        self.assertEqual(dt_before.utcoffset().total_seconds(), 3600)  # CET
        self.assertEqual(dt_after.utcoffset().total_seconds(), 7200)  # CEST

    def test_random_datetime_in_slot_respects_tz(self):
        tz = ZoneInfo("Europe/Brussels")
        day = datetime(2026, 7, 1, tzinfo=tz)
        slot = _make_slot("day_slot", 10, 0, 11, 0)
        candidate = random_datetime_in_slot(day, slot)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.tzinfo, tz)
        self.assertEqual(candidate.utcoffset().total_seconds(), 7200)


def _make_slot(slot_id: str, start_h: int, start_m: int, end_h: int, end_m: int):
    from scheduler.model import TimeSlot

    return TimeSlot(slot_id, (0, 1, 2, 3, 4, 5, 6), start_h, start_m, end_h, end_m, "default")


class PickNextExecutionDSTTests(unittest.TestCase):
    def test_pick_next_execution_survives_spring_forward(self):
        tz = ZoneInfo("Europe/Brussels")
        # A slot at 05:00-05:30 on the DST-change day must still pick a valid UTC instant.
        slots = (_make_slot("morning", 5, 0, 5, 30),)
        before = datetime(2026, 3, 29, 4, 0, tzinfo=tz)
        next_run, slot, day = pick_next_execution(before, slots)
        self.assertEqual(slot.slot_id, "morning")
        self.assertEqual(next_run.utcoffset().total_seconds(), 7200)

    def test_find_next_pending_skips_executed(self):
        tz = ZoneInfo("Europe/Brussels")
        slots = (_make_slot("daily", 8, 0, 8, 30),)
        now = datetime(2026, 4, 22, 7, 0, tzinfo=tz)
        today_key = slots[0].to_key(datetime(2026, 4, 22, tzinfo=tz))
        next_run, slot, day = find_next_pending_execution(now, slots, lambda key: key == today_key)
        # Must have skipped today and moved to tomorrow.
        self.assertEqual(day.date(), datetime(2026, 4, 23, tzinfo=tz).date())
        self.assertGreater(next_run, now)


# --- ProcessLock ---------------------------------------------------------


class ProcessLockDirectTests(unittest.TestCase):
    def test_acquire_release_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = ProcessLock(Path(tmp) / "foo.lock", stale_seconds=60)
            self.assertTrue(lock.acquire())
            self.assertTrue(lock.lock_file.exists())
            lock.release()
            self.assertFalse(lock.lock_file.exists())

    def test_second_acquire_blocks_while_first_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_file = Path(tmp) / "foo.lock"
            first = ProcessLock(lock_file, stale_seconds=999)
            second = ProcessLock(lock_file, stale_seconds=999)
            self.assertTrue(first.acquire())
            try:
                self.assertFalse(second.acquire())
            finally:
                first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_stale_recovery_by_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_file = Path(tmp) / "foo.lock"
            owner = ProcessLock(lock_file, stale_seconds=0)
            self.assertTrue(owner.acquire())
            # Drop the handle without calling release() to simulate a crash.
            os.close(owner._fd)  # noqa: SLF001 - inspecting private state is the whole point
            owner._fd = None
            # A new ProcessLock must reclaim because stale_seconds=0 triggers recovery.
            replacement = ProcessLock(lock_file, stale_seconds=0)
            self.assertTrue(replacement.acquire())
            replacement.release()


# --- HistoryStore lock ---------------------------------------------------


class HistoryStoreLockTests(unittest.TestCase):
    def test_concurrent_appends_do_not_interleave_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            store = HistoryStore(path)

            def append(idx: int) -> None:
                store.append(
                    slot_key=f"key-{idx}",
                    slot_id="slot",
                    scenario_id="scen",
                    execution_id=None,
                    executed_at=datetime(2026, 4, 22, 12, 0),
                    status="success",
                    step="noop",
                    message=f"msg-{idx}" * 200,  # Long enough to cross buffered-write boundaries.
                )

            threads = [Thread(target=append, args=(i,)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 20)
            for line in lines:
                # Every line must be a well-formed JSON document — no interleaving.
                import json

                parsed = json.loads(line)
                self.assertIn("slot_key", parsed)

    def test_prune_preserves_entries_not_older_than_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            store = HistoryStore(path)
            now = datetime.now()
            for i in range(3):
                store.append(
                    slot_key=f"k-{i}",
                    slot_id="s",
                    scenario_id="sc",
                    execution_id=None,
                    executed_at=now,
                    status="success",
                    step="n",
                    message=f"m{i}",
                )
            removed = store.prune(older_than_days=1000)
            self.assertEqual(removed, 0)
            self.assertEqual(len(store.read()), 3)


# --- try block finally_steps exception -----------------------------------


class TryBlockFinallyExceptionTests(unittest.TestCase):
    def test_finally_step_exception_propagates(self):
        # When a try block's finally_steps raise, the engine must surface
        # the finally exception — otherwise operators lose visibility of
        # cleanup failures.
        from scenarios.engine import EngineContext, execute_try_step
        from scenarios.loader import ScenarioData

        def fake_execute_scenario_step(step, **_kwargs):
            if step.type == "boom":
                raise RuntimeError("boom from finally")
            return None

        engine = EngineContext(
            operation_registry={},
            execute_atomic_step=lambda *a, **k: None,
            execute_scenario_step=fake_execute_scenario_step,
            parallel_safe_steps=frozenset(),
            driver=None,
            config=SimpleNamespace(),
            logger=SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None, success=lambda *a: None, debug=lambda *a: None),
            notifier=None,
            network_check=None,
            network_check_by_key=None,
            scenario_data=ScenarioData(pushovers={}, networks={}, default_pushover_key=None, default_network_key=None),
            context={},
            dry_run=False,
        )
        step = ScenarioStep(
            type="try",
            payload={
                "try_steps": [_atomic("ok")],
                "catch_steps": [],
                "finally_steps": [_atomic("boom")],
            },
            timeout_seconds=None,
            retry=0,
            retry_delay_seconds=0,
            retry_backoff_seconds=1,
            continue_on_error=False,
            when=None,
        )
        with self.assertRaises(RuntimeError) as ctx:
            execute_try_step(step, engine)
        self.assertIn("boom", str(ctx.exception))


def _atomic(op_type: str) -> ScenarioStep:
    return ScenarioStep(
        type=op_type,
        payload={},
        timeout_seconds=None,
        retry=0,
        retry_delay_seconds=0,
        retry_backoff_seconds=1,
        continue_on_error=False,
        when=None,
    )


# --- _run_with_timeout --------------------------------------------------


class RunWithTimeoutTests(unittest.TestCase):
    def test_timeout_raises_before_body_finishes(self):
        from scenarios.runner import _run_with_timeout

        def slow():
            time.sleep(1.0)
            return "done"

        with self.assertRaises(TimeoutError):
            _run_with_timeout(slow, timeout_seconds=0.05)

    def test_completed_within_budget_returns_result(self):
        from scenarios.runner import _run_with_timeout

        self.assertEqual(_run_with_timeout(lambda: "ok", timeout_seconds=5), "ok")


if __name__ == "__main__":
    unittest.main()
