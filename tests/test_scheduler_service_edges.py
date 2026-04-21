from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.config import AppConfig, NetworkConfig, RuntimeConfig, TaskConfig
from app.logger import Logger
from app.notifier import Notifier
from network.vpn import DetectionEvidence, DetectionResult, NetworkLocation
from scenarios.loader import ScenarioData, ScenarioDefinition
from scheduler.model import TimeSlot
from scheduler.service import SchedulerService, _build_failure_message, countdown_console


def _service(tmp: str, *, requires_network: bool = False) -> SchedulerService:
    base = Path(tmp)
    runtime = RuntimeConfig(
        timezone_name="Europe/Brussels",
        check_interval_seconds=1,
        countdown_threshold_seconds=0,
        network_retry_seconds=1,
        planning_notification_cooldown_seconds=60,
        lock_stale_seconds=100,
        state_dir=base,
        lock_file=base / "scheduler.lock",
        execution_history_file=base / "executions.json",
        next_execution_file=base / "next.json",
        last_run_file=base / "last_run.json",
        slots_file=base / "slots.json",
        scenarios_file=base / "scenarios.json",
        history_file=base / "history.jsonl",
        artifacts_dir=base / "artifacts",
        log_file=None,
        log_max_bytes=1024,
        log_backup_count=2,
    )
    scenario = ScenarioDefinition("scenario", "", steps=(), requires_enterprise_network=requires_network)
    guard = type(
        "Guard",
        (),
        {
            "is_default_network_available": lambda self, context="": True,
            "is_network_available_by_key": lambda self, key: True,
            "check_before_run": lambda self, notifier: True,
            "detect_default": lambda self, context="": DetectionResult(NetworkLocation.OFFICE, (), DetectionEvidence()),
        },
    )()
    return SchedulerService(
        config=AppConfig(
            task=TaskConfig(),
            network=NetworkConfig((), (), (), (), (), (), (), 1.0, (), True),
            runtime=runtime,
            debug_enabled=False,
        ),
        logger=Logger(debug_enabled=False),
        notifier=Notifier(None, Logger(debug_enabled=False)),
        network_guard=guard,
        slots=(TimeSlot("slot1", tuple(range(7)), 0, 0, 23, 59, "scenario"),),
        scenarios={"scenario": scenario},
        scenario_data=ScenarioData(pushovers={}, networks={}, default_pushover_key=None, default_network_key=None),
    )


class SchedulerServiceEdgeTests(unittest.TestCase):
    def test_countdown_console_immediate_and_optimized_wait_paths(self):
        tz = ZoneInfo("Europe/Brussels")
        logger = Logger(debug_enabled=False)
        with patch("scheduler.service.datetime") as datetime_mock, patch("scheduler.service.time.sleep") as sleep_mock, patch("builtins.print"):
            datetime_mock.now.return_value = datetime(2026, 4, 21, 10, 0, tzinfo=tz)
            countdown_console(datetime(2026, 4, 21, 10, 0, tzinfo=tz), 10, tz, logger)
            sleep_mock.assert_not_called()

        with patch("scheduler.service.datetime") as datetime_mock, patch("scheduler.service.time.sleep", side_effect=KeyboardInterrupt), patch("builtins.print") as printed:
            datetime_mock.now.return_value = datetime(2026, 4, 21, 10, 0, tzinfo=tz)
            with self.assertRaises(KeyboardInterrupt):
                countdown_console(datetime(2026, 4, 21, 10, 0, 5, tzinfo=tz), 10, tz, logger)
            printed.assert_called()

        with patch("scheduler.service.datetime") as datetime_mock, patch("scheduler.service.time.sleep", side_effect=[None, KeyboardInterrupt]), patch("builtins.print"):
            datetime_mock.now.return_value = datetime(2026, 4, 21, 10, 0, tzinfo=tz)
            with self.assertRaises(KeyboardInterrupt):
                countdown_console(datetime(2026, 4, 21, 10, 1, tzinfo=tz), 10, tz, logger)

    def test_runtime_history_plan_and_alert_helpers(self):
        with TemporaryDirectory() as tmp:
            service = _service(tmp)
            now = datetime.now(service.runtime.timezone).replace(microsecond=0)
            service.history_store.append(
                slot_key="slot-key",
                slot_id="slot1",
                scenario_id="scenario",
                execution_id="exec",
                executed_at=now,
                status="success",
                step="done",
                message="ok",
            )

            self.assertEqual(service.describe_plan_for_scenarios({"scenario"})["scenario_id"], "scenario")
            with self.assertRaises(RuntimeError):
                service.describe_plan_for_scenarios({"missing"})
            self.assertEqual(service.dump_runtime()["slots_count"], 1)
            self.assertEqual(service.read_history(status="success")[0]["execution_id"], "exec")
            self.assertEqual(service.prune_history(older_than_days=0), 1)
            self.assertTrue(service._should_send_network_alert(now, None, 60))
            self.assertFalse(service._should_send_network_alert(now, now, 60))
            self.assertTrue(service._should_send_network_alert(now, now - timedelta(seconds=61), 60))

    def test_run_modes_success_failure_and_no_eligible_slots(self):
        success = SimpleNamespace(success=True, step="done", message="ok", execution_id="exec", screenshot_path=None, page_source_path=None)
        failure = SimpleNamespace(success=False, step="boom", message="ko", execution_id="exec-fail", screenshot_path="screen.png", page_source_path="page.html")

        with TemporaryDirectory() as tmp:
            service = _service(tmp)
            with patch("scheduler.service.run_task", return_value=success), patch("scheduler.service.pretty_print_result"):
                self.assertEqual(service.run_check_mode(dry_run=False), 0)
                self.assertEqual(service.run_slot("slot1", dry_run=True), 0)
                self.assertEqual(service.run_scenario("scenario", dry_run=True), 0)
                self.assertEqual(service.run_next_for_scenarios({"scenario"}, dry_run=True), 0)
            with patch("scheduler.service.run_task", return_value=failure):
                self.assertEqual(service.run_slot("slot1", dry_run=True), 2)
                self.assertEqual(service.run_scenario("scenario", dry_run=True), 2)
                self.assertEqual(service.run_next_for_scenarios({"scenario"}, dry_run=True), 2)
            self.assertEqual(service.run_slot("missing", dry_run=True), 1)
            self.assertEqual(service.run_next_for_scenarios({"missing"}, dry_run=True), 1)
            self.assertIn("screenshot=screen.png", _build_failure_message(failure))
            self.assertIn("page_source=page.html", _build_failure_message(failure))


if __name__ == "__main__":
    unittest.main()
