import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.config import AppConfig, NetworkConfig, RuntimeConfig, TaskConfig
from app.logger import Logger
from app.main import validate_data_defaults, validate_slot_scenarios
from app.notifier import Notifier
from network.guard import NetworkGuard
from scenarios.loader import ScenarioData, ScenarioDefinition
from scheduler.model import TimeSlot
from scheduler.service import SchedulerService


class ServiceTests(unittest.TestCase):
    def test_validate_slot_scenarios_raises_on_missing_scenario(self):
        slots = (TimeSlot("slot1", (0,), 8, 0, 8, 15, "missing"),)
        with self.assertRaises(ValueError):
            validate_slot_scenarios(slots, {})

    def test_validate_data_defaults_accepts_complete_named_data(self):
        data = ScenarioData(
            pushovers={},
            networks={"default": NetworkConfig((), (), (), (), (), (), (), 1.0, (), True)},
            default_network_key="default",
            default_pushover_key=None,
        )
        validate_data_defaults(data)

    def test_network_guard_checks_by_key(self):
        config = AppConfig(
            task=TaskConfig(),
            network=NetworkConfig((), (), (), (), (), (), (), 1.0, (), True),
            runtime=RuntimeConfig(
                timezone_name="Europe/Brussels",
                check_interval_seconds=10,
                countdown_threshold_seconds=300,
                network_retry_seconds=10,
                planning_notification_cooldown_seconds=900,
                lock_stale_seconds=100,
                state_dir=Path("."),
                lock_file=Path("scheduler.lock"),
                execution_history_file=Path("executions.json"),
                next_execution_file=Path("next.json"),
                last_run_file=Path("last_run.json"),
                slots_file=Path("config/slots.json"),
                scenarios_file=Path("config/scenarios.json"),
                history_file=Path("history.jsonl"),
                artifacts_dir=Path("artifacts"),
                log_file=None,
                log_max_bytes=1024,
                log_backup_count=2,
            ),
            debug_enabled=False,
        )
        data = ScenarioData(
            pushovers={},
            networks={"office": NetworkConfig((), (), (), (), (), (), (), 1.0, (), True)},
            default_network_key="office",
            default_pushover_key=None,
        )
        guard = NetworkGuard(config, data, Logger(debug_enabled=False))
        with self.assertRaises(ValueError):
            guard.is_network_available_by_key("missing")

    def test_scenario_definition_keeps_network_requirement_flag(self):
        definition = ScenarioDefinition(
            scenario_id="id",
            description="",
            steps=(),
            requires_enterprise_network=True,
        )
        self.assertTrue(definition.requires_enterprise_network)

    def test_scheduler_service_describe_plan_returns_payload(self):
        with TemporaryDirectory() as tmp:
            runtime = RuntimeConfig(
                timezone_name="Europe/Brussels",
                check_interval_seconds=10,
                countdown_threshold_seconds=300,
                network_retry_seconds=10,
                planning_notification_cooldown_seconds=900,
                lock_stale_seconds=100,
                state_dir=Path(tmp),
                lock_file=Path(tmp) / "scheduler.lock",
                execution_history_file=Path(tmp) / "executions.json",
                next_execution_file=Path(tmp) / "next.json",
                last_run_file=Path(tmp) / "last_run.json",
                slots_file=Path("config/slots.json"),
                scenarios_file=Path("config/scenarios.json"),
                history_file=Path(tmp) / "history.jsonl",
                artifacts_dir=Path(tmp) / "artifacts",
                log_file=None,
                log_max_bytes=1024,
                log_backup_count=2,
            )
            config = AppConfig(
                task=TaskConfig(),
                network=NetworkConfig((), (), (), (), (), (), (), 1.0, (), True),
                runtime=runtime,
                debug_enabled=False,
            )
            data = ScenarioData(
                pushovers={},
                networks={"office": NetworkConfig((), (), (), (), (), (), (), 1.0, (), True)},
                default_network_key="office",
                default_pushover_key=None,
            )
            scenario = ScenarioDefinition("scenario", "", steps=(), requires_enterprise_network=False)
            service = SchedulerService(
                config=config,
                logger=Logger(debug_enabled=False),
                notifier=Notifier(None, Logger(debug_enabled=False)),
                network_guard=type("Guard", (), {"is_default_network_available": lambda self, context="": True})(),
                slots=(TimeSlot("slot1", (0, 1, 2, 3, 4, 5, 6), 0, 0, 23, 59, "scenario"),),
                scenarios={"scenario": scenario},
                scenario_data=data,
            )
            payload = service.describe_plan()
            self.assertEqual(payload["scenario_id"], "scenario")

    def test_create_logger_uses_runtime_log_file(self):
        from app.main import create_logger

        with TemporaryDirectory() as tmp:
            runtime = RuntimeConfig(
                timezone_name="Europe/Brussels",
                check_interval_seconds=10,
                countdown_threshold_seconds=300,
                network_retry_seconds=10,
                planning_notification_cooldown_seconds=900,
                lock_stale_seconds=100,
                state_dir=Path(tmp),
                lock_file=Path(tmp) / "scheduler.lock",
                execution_history_file=Path(tmp) / "executions.json",
                next_execution_file=Path(tmp) / "next.json",
                last_run_file=Path(tmp) / "last_run.json",
                slots_file=Path("config/slots.json"),
                scenarios_file=Path("config/scenarios.json"),
                history_file=Path(tmp) / "history.jsonl",
                artifacts_dir=Path(tmp) / "artifacts",
                log_file=Path(tmp) / "app.log",
                log_max_bytes=10,
                log_backup_count=2,
            )
            config = AppConfig(
                task=TaskConfig(),
                network=NetworkConfig((), (), (), (), (), (), (), 1.0, (), True),
                runtime=runtime,
                debug_enabled=False,
            )
            logger = create_logger(config)
            logger.info("hello")
            self.assertIn("hello", (Path(tmp) / "app.log").read_text(encoding="utf-8"))
            logger.info("world world world")
            self.assertTrue((Path(tmp) / "app.log.1").exists())

    def test_scheduler_service_run_scenario_unknown_returns_1(self):
        with TemporaryDirectory() as tmp:
            runtime = RuntimeConfig(
                timezone_name="Europe/Brussels",
                check_interval_seconds=10,
                countdown_threshold_seconds=300,
                network_retry_seconds=10,
                planning_notification_cooldown_seconds=900,
                lock_stale_seconds=100,
                state_dir=Path(tmp),
                lock_file=Path(tmp) / "scheduler.lock",
                execution_history_file=Path(tmp) / "executions.json",
                next_execution_file=Path(tmp) / "next.json",
                last_run_file=Path(tmp) / "last_run.json",
                slots_file=Path("config/slots.json"),
                scenarios_file=Path("config/scenarios.json"),
                history_file=Path(tmp) / "history.jsonl",
                artifacts_dir=Path(tmp) / "artifacts",
                log_file=None,
                log_max_bytes=1024,
                log_backup_count=2,
            )
            config = AppConfig(
                task=TaskConfig(),
                network=NetworkConfig((), (), (), (), (), (), (), 1.0, (), True),
                runtime=runtime,
                debug_enabled=False,
            )
            service = SchedulerService(
                config=config,
                logger=Logger(debug_enabled=False),
                notifier=Notifier(None, Logger(debug_enabled=False)),
                network_guard=type("Guard", (), {"is_default_network_available": lambda self, context="": True, "is_network_available_by_key": lambda self, key: True})(),
                slots=(),
                scenarios={},
                scenario_data=ScenarioData(pushovers={}, networks={}, default_pushover_key=None, default_network_key=None),
            )
            self.assertEqual(service.run_scenario("missing", dry_run=True), 1)

    def test_scheduler_service_listings(self):
        with TemporaryDirectory() as tmp:
            runtime = RuntimeConfig(
                timezone_name="Europe/Brussels",
                check_interval_seconds=10,
                countdown_threshold_seconds=300,
                network_retry_seconds=10,
                planning_notification_cooldown_seconds=900,
                lock_stale_seconds=100,
                state_dir=Path(tmp),
                lock_file=Path(tmp) / "scheduler.lock",
                execution_history_file=Path(tmp) / "executions.json",
                next_execution_file=Path(tmp) / "next.json",
                last_run_file=Path(tmp) / "last_run.json",
                slots_file=Path("config/slots.json"),
                scenarios_file=Path("config/scenarios.json"),
                history_file=Path(tmp) / "history.jsonl",
                artifacts_dir=Path(tmp) / "artifacts",
                log_file=None,
                log_max_bytes=1024,
                log_backup_count=2,
            )
            config = AppConfig(
                task=TaskConfig(),
                network=NetworkConfig((), (), (), (), (), (), (), 1.0, (), True),
                runtime=runtime,
                debug_enabled=False,
            )
            scenario = ScenarioDefinition("scenario", "", steps=(), requires_enterprise_network=False)
            service = SchedulerService(
                config=config,
                logger=Logger(debug_enabled=False),
                notifier=Notifier(None, Logger(debug_enabled=False)),
                network_guard=type("Guard", (), {"is_default_network_available": lambda self, context="": True, "is_network_available_by_key": lambda self, key: True})(),
                slots=(TimeSlot("slot1", (0,), 8, 0, 8, 15, "scenario"),),
                scenarios={"scenario": scenario},
                scenario_data=ScenarioData(pushovers={}, networks={}, default_pushover_key=None, default_network_key=None),
            )
            self.assertEqual(service.list_slots()[0]["slot_id"], "slot1")
            self.assertEqual(service.list_scenarios()[0]["scenario_id"], "scenario")

    def test_scheduler_service_run_next_returns_success(self):
        with TemporaryDirectory() as tmp:
            runtime = RuntimeConfig(
                timezone_name="Europe/Brussels",
                check_interval_seconds=10,
                countdown_threshold_seconds=300,
                network_retry_seconds=10,
                planning_notification_cooldown_seconds=900,
                lock_stale_seconds=100,
                state_dir=Path(tmp),
                lock_file=Path(tmp) / "scheduler.lock",
                execution_history_file=Path(tmp) / "executions.json",
                next_execution_file=Path(tmp) / "next.json",
                last_run_file=Path(tmp) / "last_run.json",
                slots_file=Path("config/slots.json"),
                scenarios_file=Path("config/scenarios.json"),
                history_file=Path(tmp) / "history.jsonl",
                artifacts_dir=Path(tmp) / "artifacts",
                log_file=None,
                log_max_bytes=1024,
                log_backup_count=2,
            )
            config = AppConfig(
                task=TaskConfig(),
                network=NetworkConfig((), (), (), (), (), (), (), 1.0, (), True),
                runtime=runtime,
                debug_enabled=False,
            )
            scenario = ScenarioDefinition("scenario", "", steps=(), requires_enterprise_network=False)
            service = SchedulerService(
                config=config,
                logger=Logger(debug_enabled=False),
                notifier=Notifier(None, Logger(debug_enabled=False)),
                network_guard=type("Guard", (), {"is_default_network_available": lambda self, context="": True, "is_network_available_by_key": lambda self, key: True})(),
                slots=(TimeSlot("slot1", (0, 1, 2, 3, 4, 5, 6), 8, 0, 8, 15, "scenario"),),
                scenarios={"scenario": scenario},
                scenario_data=ScenarioData(pushovers={}, networks={}, default_pushover_key=None, default_network_key=None),
            )
            with patch(
                "scheduler.service.run_task",
                return_value=type("R", (), {"success": True, "step": "done", "message": "ok", "execution_id": "exec", "screenshot_path": None, "page_source_path": None})(),
            ):
                self.assertEqual(service.run_next(dry_run=True), 0)
