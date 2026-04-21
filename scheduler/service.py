from __future__ import annotations

import time
import traceback
from datetime import datetime
from uuid import uuid4

from app.config import AppConfig
from app.logger import Logger
from app.notifier import Notifier
from network.guard import NetworkGuard
from network.vpn import pretty_print_result
from scenarios.loader import ScenarioData, ScenarioDefinition
from scenarios.runner import TaskRunResult, run_task
from scheduler.model import TimeSlot, find_next_pending_execution, format_remaining
from state.store import ExecutionStateStore, HistoryStore, LastRunStore, NextExecutionStore, ProcessLock


def countdown_console(target: datetime, threshold_seconds: int, tz, logger: Logger) -> None:
    while True:
        now = datetime.now(tz).replace(microsecond=0)
        remaining = int((target - now).total_seconds())

        if remaining <= 0:
            print(f"\rHeure actuelle: {now.strftime('%Y-%m-%d %H:%M:%S')} | Instant choisi: {target.strftime('%Y-%m-%d %H:%M:%S')} | GO!{' ' * 20}")
            return

        if remaining > threshold_seconds:
            sleep_for = remaining - threshold_seconds
            logger.info(f"Attente optimisee avant countdown: {sleep_for}s")
            time.sleep(sleep_for)
            continue

        print(
            f"\rHeure actuelle: {now.strftime('%Y-%m-%d %H:%M:%S')} | Instant choisi: {target.strftime('%Y-%m-%d %H:%M:%S')} | Temps restant: {format_remaining(remaining)}",
            end="",
            flush=True,
        )
        time.sleep(1)


class SchedulerService:
    def __init__(
        self,
        config: AppConfig,
        logger: Logger,
        notifier: Notifier,
        network_guard: NetworkGuard,
        slots: tuple[TimeSlot, ...],
        scenarios: dict[str, ScenarioDefinition],
        scenario_data: ScenarioData,
    ):
        self.config = config
        self.logger = logger
        self.notifier = notifier
        self.network_guard = network_guard
        self.slots = slots
        self.scenarios = scenarios
        self.scenario_data = scenario_data
        self.runtime = config.runtime
        self.state_store = ExecutionStateStore(self.runtime.execution_history_file)
        self.next_execution_store = NextExecutionStore(self.runtime.next_execution_file)
        self.history_store = HistoryStore(self.runtime.history_file)
        self.last_run_store = LastRunStore(self.runtime.last_run_file)

    def choose_next_slot(self, now: datetime) -> tuple[datetime, TimeSlot, datetime]:
        return find_next_pending_execution(now, self.slots, self.state_store.has_executed)

    def describe_plan(self) -> dict[str, object]:
        now = datetime.now(self.runtime.timezone).replace(microsecond=0)
        next_run, slot, day = self.choose_next_slot(now)
        return self._describe_plan_payload(now, next_run, slot, day)

    def describe_plan_for_scenarios(self, scenario_ids: set[str]) -> dict[str, object]:
        eligible_slots = tuple(slot for slot in self.slots if slot.scenario_id in scenario_ids)
        if not eligible_slots:
            raise RuntimeError("Aucun slot disponible pour ces scenarios.")
        now = datetime.now(self.runtime.timezone).replace(microsecond=0)
        next_run, slot, day = find_next_pending_execution(
            now,
            eligible_slots,
            self.state_store.has_executed,
        )
        return self._describe_plan_payload(now, next_run, slot, day)

    def _describe_plan_payload(self, now: datetime, next_run: datetime, slot: TimeSlot, day: datetime) -> dict[str, object]:
        scenario = self.scenarios[slot.scenario_id]
        return {
            "generated_at": now.isoformat(),
            "timezone": self.runtime.timezone_name,
            "slot_key": slot.to_key(day),
            "slot_id": slot.slot_id,
            "scenario_id": scenario.scenario_id,
            "scheduled_for": next_run.isoformat(),
            "requires_enterprise_network": scenario.requires_enterprise_network,
            "before_steps": len(scenario.before_steps),
            "steps": len(scenario.steps),
            "on_success": len(scenario.on_success_steps),
            "on_failure": len(scenario.on_failure_steps),
            "finally_steps": len(scenario.finally_steps),
            "default_pushover_key": self.scenario_data.default_pushover_key,
            "default_network_key": self.scenario_data.default_network_key,
            "default_network_available": self.network_guard.is_default_network_available("pendant plan"),
        }

    def list_slots(self) -> list[dict[str, object]]:
        return [
            {
                "slot_id": slot.slot_id,
                "days": list(slot.days),
                "start": f"{slot.start_hour:02d}:{slot.start_minute:02d}",
                "end": f"{slot.end_hour:02d}:{slot.end_minute:02d}",
                "scenario_id": slot.scenario_id,
            }
            for slot in self.slots
        ]

    def list_scenarios(self) -> list[dict[str, object]]:
        return [
            {
                "scenario_id": scenario.scenario_id,
                "requires_enterprise_network": scenario.requires_enterprise_network,
                "before_steps": len(scenario.before_steps),
                "steps": len(scenario.steps),
                "on_success": len(scenario.on_success_steps),
                "on_failure": len(scenario.on_failure_steps),
                "finally_steps": len(scenario.finally_steps),
            }
            for scenario in self.scenarios.values()
        ]

    def dump_runtime(self) -> dict[str, object]:
        return {
            "timezone": self.runtime.timezone_name,
            "state_dir": str(self.runtime.state_dir),
            "slots_file": str(self.runtime.slots_file),
            "scenarios_file": str(self.runtime.scenarios_file),
            "execution_history_file": str(self.runtime.execution_history_file),
            "next_execution_file": str(self.runtime.next_execution_file),
            "last_run_file": str(self.runtime.last_run_file),
            "history_file": str(self.runtime.history_file),
            "artifacts_dir": str(self.runtime.artifacts_dir),
            "log_file": str(self.runtime.log_file) if self.runtime.log_file else None,
            "log_max_bytes": self.runtime.log_max_bytes,
            "log_backup_count": self.runtime.log_backup_count,
            "default_pushover_key": self.scenario_data.default_pushover_key,
            "default_network_key": self.scenario_data.default_network_key,
            "slots_count": len(self.slots),
            "scenarios_count": len(self.scenarios),
        }

    def read_history(
        self,
        *,
        limit: int = 20,
        status: str | None = None,
        slot_id: str | None = None,
        scenario_id: str | None = None,
        execution_id: str | None = None,
    ) -> list[dict]:
        return self.history_store.read(
            limit=limit,
            status=status,
            slot_id=slot_id,
            scenario_id=scenario_id,
            execution_id=execution_id,
        )

    def prune_history(self, *, older_than_days: int) -> int:
        return self.history_store.prune(older_than_days=older_than_days)

    def run_check_mode(self, dry_run: bool = True) -> int:
        del dry_run
        self.logger.info("Mode check actif.")
        self.logger.info(f"Timezone active: {self.runtime.timezone}")
        self.logger.info(f"Notifications actives: {self.notifier.is_enabled()}")
        network_result = self.network_guard.detect_default("pendant check")

        pretty_print_result(network_result)

        now = datetime.now(self.runtime.timezone).replace(microsecond=0)
        next_run, slot, day = self.choose_next_slot(now)
        slot_key = slot.to_key(day)
        self.logger.info(f"Prochain slot en attente: {slot_key}")
        self.logger.info(f"Prochaine execution planifiee: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")

        scenario = self.scenarios[slot.scenario_id]
        self.logger.info(f"Scenario associe: {scenario.scenario_id}")
        task_result = run_task(
            self.config.task,
            self.logger,
            scenario=scenario,
            scenario_data=self.scenario_data,
            dry_run=True,
            notifier=self.notifier,
            network_check=lambda: self.network_guard.is_default_network_available(),
            network_check_by_key=self.network_guard.is_network_available_by_key,
            initial_context={
                "slot_key": slot_key,
                "slot_id": slot.slot_id,
                "scenario_id": scenario.scenario_id,
                "scheduled_for": next_run.isoformat(),
                "executed_at": now.isoformat(),
            },
        )
        self.logger.info(f"Test tache: success={task_result.success} step={task_result.step} message={task_result.message}")
        return 0

    def run_slot(self, slot_id: str, dry_run: bool) -> int:
        slot = next((item for item in self.slots if item.slot_id == slot_id), None)
        if slot is None:
            self.logger.error(f"Slot introuvable: {slot_id}")
            return 1
        scenario = self.scenarios[slot.scenario_id]
        execution_time = datetime.now(self.runtime.timezone).replace(microsecond=0)
        slot_key = slot.to_key(execution_time)
        result = self._run_once(slot, scenario, slot_key, execution_time, dry_run=dry_run)
        return 0 if result.success else 2

    def run_next(self, dry_run: bool) -> int:
        execution_time = datetime.now(self.runtime.timezone).replace(microsecond=0)
        next_run, slot, day = self.choose_next_slot(execution_time)
        slot_key = slot.to_key(day)
        scenario = self.scenarios[slot.scenario_id]
        self.logger.info(f"Execution immediate du prochain slot: {slot.slot_id} -> {scenario.scenario_id}")
        result = self._run_once(
            slot,
            scenario,
            slot_key,
            execution_time,
            dry_run=dry_run,
            scheduled_for=next_run,
        )
        self._handle_task_result(result, execution_time, slot, scenario, slot_key)
        return 0 if result.success else 2

    def run_next_for_scenarios(self, scenario_ids: set[str], dry_run: bool) -> int:
        eligible_slots = tuple(slot for slot in self.slots if slot.scenario_id in scenario_ids)
        if not eligible_slots:
            self.logger.error("Aucun slot disponible pour ces scenarios.")
            return 1
        execution_time = datetime.now(self.runtime.timezone).replace(microsecond=0)
        next_run, slot, day = find_next_pending_execution(
            execution_time,
            eligible_slots,
            self.state_store.has_executed,
        )
        slot_key = slot.to_key(day)
        scenario = self.scenarios[slot.scenario_id]
        self.logger.info(f"Execution immediate du prochain slot utilisateur: {slot.slot_id} -> {scenario.scenario_id}")
        result = self._run_once(
            slot,
            scenario,
            slot_key,
            execution_time,
            dry_run=dry_run,
            scheduled_for=next_run,
        )
        self._handle_task_result(result, execution_time, slot, scenario, slot_key)
        return 0 if result.success else 2

    def run_scenario(self, scenario_id: str, dry_run: bool) -> int:
        scenario = self.scenarios.get(scenario_id)
        if scenario is None:
            self.logger.error(f"Scenario introuvable: {scenario_id}")
            return 1
        execution_time = datetime.now(self.runtime.timezone).replace(microsecond=0)
        synthetic_slot = TimeSlot("__manual__", tuple(range(7)), execution_time.hour, execution_time.minute, 23, 59, scenario_id)
        slot_key = synthetic_slot.to_key(execution_time)
        result = self._run_once(synthetic_slot, scenario, slot_key, execution_time, dry_run=dry_run)
        return 0 if result.success else 2

    def loop(self, dry_run: bool, once: bool) -> int:
        network_alert_sent_at: datetime | None = None
        last_planned_slot_key: str | None = None

        with ProcessLock(self.runtime.lock_file, stale_seconds=self.runtime.lock_stale_seconds).held() as acquired:
            if not acquired:
                self.logger.error("Une autre instance du scheduler est deja active.")
                return 1

            self.logger.info("Scheduler demarre.")
            self.logger.info(f"Timezone active: {self.runtime.timezone}")

            while True:
                try:
                    now = datetime.now(self.runtime.timezone).replace(microsecond=0)
                    next_run, slot, day = self.choose_next_slot(now)
                    slot_key = slot.to_key(day)
                    scenario = self.scenarios[slot.scenario_id]

                    if scenario.requires_enterprise_network and not self.network_guard.is_default_network_available():
                        self.next_execution_store.save(
                            slot_key,
                            next_run,
                            status="blocked_no_network",
                            details="Connexion au reseau d'entreprise ou VPN absente.",
                            slot_id=slot.slot_id,
                            scenario_id=scenario.scenario_id,
                        )
                        if self._should_send_network_alert(
                            now,
                            network_alert_sent_at,
                            self.runtime.planning_notification_cooldown_seconds,
                        ):
                            message = "Planification suspendue: machine non connectee au reseau d'entreprise ou au VPN."
                            self.notifier.send(message)
                            self.logger.warning(message)
                            network_alert_sent_at = now
                        time.sleep(self.runtime.network_retry_seconds)
                        continue

                    network_alert_sent_at = None
                    if slot_key != last_planned_slot_key:
                        self.next_execution_store.save(
                            slot_key,
                            next_run,
                            status="planned",
                            slot_id=slot.slot_id,
                            scenario_id=scenario.scenario_id,
                        )
                        message = f"Prochaine execution planifiee: {next_run.strftime('%Y-%m-%d %H:%M:%S')} ({slot.slot_id} -> {scenario.scenario_id})"
                        self.notifier.send(message)
                        self.logger.info(message)
                        last_planned_slot_key = slot_key

                    countdown_console(
                        next_run,
                        self.runtime.countdown_threshold_seconds,
                        self.runtime.timezone,
                        self.logger,
                    )

                    if self.state_store.has_executed(slot_key):
                        self.next_execution_store.clear()
                        self.logger.warning(f"Slot deja execute, saut: {slot_key}")
                        time.sleep(self.runtime.check_interval_seconds)
                        continue

                    if scenario.requires_enterprise_network and not self.network_guard.check_before_run(self.notifier):
                        self.next_execution_store.save(
                            slot_key,
                            next_run,
                            status="blocked_no_network",
                            details="Connexion perdue juste avant execution.",
                            slot_id=slot.slot_id,
                            scenario_id=scenario.scenario_id,
                        )
                        if once:
                            return 2
                        time.sleep(self.runtime.network_retry_seconds)
                        continue

                    execution_time = datetime.now(self.runtime.timezone).replace(microsecond=0)
                    result = self._run_once(
                        slot,
                        scenario,
                        slot_key,
                        execution_time,
                        dry_run=dry_run,
                        scheduled_for=next_run,
                    )
                    self._handle_task_result(result, execution_time, slot, scenario, slot_key)
                    if once:
                        return 0 if result.success else 2
                    time.sleep(self.runtime.check_interval_seconds)
                except KeyboardInterrupt:
                    print()
                    self.logger.warning("Arret demande par l'utilisateur.")
                    return 0
                except Exception as exc:
                    print()
                    self.logger.error(f"[ERREUR] {exc}")
                    traceback.print_exc()
                    self.logger.warning("Nouvelle tentative dans 10 secondes...")
                    time.sleep(10)

    def _run_once(
        self,
        slot: TimeSlot,
        scenario: ScenarioDefinition,
        slot_key: str,
        execution_time: datetime,
        *,
        dry_run: bool,
        scheduled_for: datetime | None = None,
    ) -> TaskRunResult:
        execution_id = uuid4().hex
        self.next_execution_store.save(
            slot_key,
            execution_time,
            status="running",
            slot_id=slot.slot_id,
            scenario_id=scenario.scenario_id,
            execution_id=execution_id,
        )
        return run_task(
            self.config.task,
            self.logger,
            scenario=scenario,
            scenario_data=self.scenario_data,
            dry_run=dry_run,
            notifier=self.notifier,
            network_check=lambda: self.network_guard.is_default_network_available(),
            network_check_by_key=self.network_guard.is_network_available_by_key,
            initial_context={
                "slot_key": slot_key,
                "slot_id": slot.slot_id,
                "scenario_id": scenario.scenario_id,
                "execution_id": execution_id,
                "scheduled_for": (scheduled_for or execution_time).isoformat(),
                "executed_at": execution_time.isoformat(),
            },
            artifacts_dir=self.runtime.artifacts_dir,
        )

    def _handle_task_result(
        self,
        result: TaskRunResult,
        now: datetime,
        slot: TimeSlot,
        scenario: ScenarioDefinition,
        slot_key: str,
    ) -> None:
        if result.success:
            self.state_store.mark_executed(slot_key, now)
            self.next_execution_store.clear()
            self.last_run_store.save(
                slot_key=slot_key,
                slot_id=slot.slot_id,
                scenario_id=scenario.scenario_id,
                execution_id=result.execution_id,
                executed_at=now,
                status="success",
                step=result.step,
                message=result.message,
            )
            self.history_store.append(
                slot_key=slot_key,
                slot_id=slot.slot_id,
                scenario_id=scenario.scenario_id,
                execution_id=result.execution_id,
                executed_at=now,
                status="success",
                step=result.step,
                message=result.message,
            )
            self.logger.success(result.message)
            return

        self.next_execution_store.save(
            slot_key,
            now,
            status="failed",
            details=result.message,
            slot_id=slot.slot_id,
            scenario_id=scenario.scenario_id,
            execution_id=result.execution_id,
        )
        self.last_run_store.save(
            slot_key=slot_key,
            slot_id=slot.slot_id,
            scenario_id=scenario.scenario_id,
            execution_id=result.execution_id,
            executed_at=now,
            status="failed",
            step=result.step,
            message=_build_failure_message(result),
        )
        self.history_store.append(
            slot_key=slot_key,
            slot_id=slot.slot_id,
            scenario_id=scenario.scenario_id,
            execution_id=result.execution_id,
            executed_at=now,
            status="failed",
            step=result.step,
            message=_build_failure_message(result),
        )
        self.logger.error(f"Echec de la tache sur {slot_key}: {result.message}")

    @staticmethod
    def _should_send_network_alert(now: datetime, last_sent_at: datetime | None, cooldown_seconds: int) -> bool:
        if last_sent_at is None:
            return True
        return (now - last_sent_at).total_seconds() >= cooldown_seconds


def _build_failure_message(result: TaskRunResult) -> str:
    parts = [result.message]
    if result.screenshot_path:
        parts.append(f"screenshot={result.screenshot_path}")
    if result.page_source_path:
        parts.append(f"page_source={result.page_source_path}")
    return " | ".join(parts)
