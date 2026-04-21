from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import truststore

from app.config import AppConfig, load_config
from app.logger import Logger
from app.notifier import Notifier
from network.guard import NetworkGuard
from scenarios.loader import ScenarioDefinition, load_scenario_data, load_scenarios, load_slots
from scheduler.model import TimeSlot
from scheduler.service import SchedulerService

truststore.inject_into_ssl()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Affiche les diagnostics et quitte.")
    parser.add_argument("--validate-config", action="store_true", help="Valide la configuration JSON et quitte.")
    parser.add_argument("--validate-examples", action="store_true", help="Valide tous les fichiers JSON du dossier examples.")
    parser.add_argument("--plan", action="store_true", help="Affiche le prochain plan d execution et quitte.")
    parser.add_argument("--dump-runtime", action="store_true", help="Affiche la configuration runtime resolue et quitte.")
    parser.add_argument("--list-slots", action="store_true", help="Liste les slots configures et quitte.")
    parser.add_argument("--list-scenarios", action="store_true", help="Liste les scenarios configures et quitte.")
    parser.add_argument("--history", action="store_true", help="Affiche l'historique et quitte.")
    parser.add_argument("--history-limit", type=int, default=20, help="Nombre max de lignes pour --history.")
    parser.add_argument("--history-status", help="Filtre status pour --history.")
    parser.add_argument("--history-slot-id", help="Filtre slot pour --history.")
    parser.add_argument("--history-scenario-id", help="Filtre scenario pour --history.")
    parser.add_argument("--history-execution-id", help="Filtre execution_id pour --history.")
    parser.add_argument("--prune-history-days", type=int, help="Supprime l'historique plus ancien que N jours et quitte.")
    parser.add_argument("--run-slot", help="Execute immediatement un slot cible.")
    parser.add_argument("--run-scenario", help="Execute immediatement un scenario cible.")
    parser.add_argument("--run-next", action="store_true", help="Execute immediatement le prochain slot calcule.")
    parser.add_argument("--export-plan", help="Exporte le plan calcule vers un fichier JSON puis quitte.")
    parser.add_argument("--dry-run", action="store_true", help="N execute pas Selenium.")
    parser.add_argument("--once", action="store_true", help="Execute au plus un slot puis quitte.")
    return parser.parse_args()


def create_logger(config: AppConfig) -> Logger:
    return Logger(
        debug_enabled=config.debug_enabled,
        log_file=config.runtime.log_file,
        max_bytes=config.runtime.log_max_bytes,
        backup_count=config.runtime.log_backup_count,
        json_enabled=config.runtime.log_json,
    )


def validate_config(base_config: AppConfig, logger: Logger) -> int:
    try:
        scenario_data = load_scenario_data(base_config.runtime.scenarios_file)
        slots = load_slots(base_config.runtime.slots_file)
        scenarios = load_scenarios(base_config.runtime.scenarios_file)
        validate_slot_scenarios(slots, scenarios)
        validate_data_defaults(scenario_data)
    except Exception as exc:
        logger.error(f"Configuration invalide: {exc}")
        return 1

    logger.success("Configuration valide.")
    logger.info(f"slots.json: {base_config.runtime.slots_file}")
    logger.info(f"scenarios.json: {base_config.runtime.scenarios_file}")
    logger.info("schemas: schemas/slots.schema.json + schemas/scenarios.schema.json")
    return 0


def validate_examples(base_config: AppConfig, logger: Logger) -> int:
    from pathlib import Path

    examples_dir = Path("examples")
    scenario_files = sorted(path for path in examples_dir.glob("*.json") if path.name != "slots.json")
    slots_file = examples_dir / "slots.json"
    if not scenario_files and not slots_file.exists():
        logger.warning("Aucun example JSON trouve.")
        return 0
    try:
        for file_path in scenario_files:
            load_scenario_data(file_path)
            load_scenarios(file_path)
        if slots_file.exists():
            load_slots(slots_file)
    except Exception as exc:
        logger.error(f"Examples invalides: {exc}")
        return 1
    logger.success(f"Examples valides: {len(scenario_files) + (1 if slots_file.exists() else 0)} fichier(s).")
    return 0


def build_runtime_services(base_config: AppConfig):
    slots = load_slots(base_config.runtime.slots_file)
    scenarios = load_scenarios(base_config.runtime.scenarios_file)
    return build_runtime_services_from_catalog(base_config, slots, scenarios)


def build_runtime_services_from_catalog(base_config: AppConfig, slots, scenarios):
    scenario_data = load_scenario_data(base_config.runtime.scenarios_file)
    validate_data_defaults(scenario_data)
    validate_slot_scenarios(slots, scenarios)
    network_config = scenario_data.networks[scenario_data.default_network_key] if scenario_data.default_network_key is not None else base_config.network
    config = replace(base_config, network=network_config)
    logger = create_logger(config)
    notifier = _build_notifier(logger, scenario_data)
    network_guard = NetworkGuard(config, scenario_data, logger)
    return SchedulerService(
        config=config,
        logger=logger,
        notifier=notifier,
        network_guard=network_guard,
        slots=slots,
        scenarios=scenarios,
        scenario_data=scenario_data,
    )


def _build_notifier(logger: Logger, scenario_data) -> Notifier:
    pushover_config = None
    if scenario_data.default_pushover_key is not None:
        pushover_config = scenario_data.pushovers[scenario_data.default_pushover_key]
    return Notifier(pushover_config, logger)


def validate_slot_scenarios(slots: tuple[TimeSlot, ...], scenarios: dict[str, ScenarioDefinition]) -> None:
    missing = sorted({slot.scenario_id for slot in slots if slot.scenario_id not in scenarios})
    if missing:
        raise ValueError(f"Scenario(s) introuvable(s) pour les slots: {', '.join(missing)}")


def validate_data_defaults(scenario_data) -> None:
    if scenario_data.pushovers and scenario_data.default_pushover_key is None:
        raise ValueError("Une configuration Pushover par defaut est requise si data.pushovers est renseigne.")
    if scenario_data.networks and scenario_data.default_network_key is None:
        raise ValueError("Une configuration reseau par defaut est requise si data.networks est renseigne.")


def main() -> int:
    args = parse_args()
    base_config = load_config()
    if args.validate_config:
        return validate_config(base_config, create_logger(base_config))
    if args.validate_examples:
        return validate_examples(base_config, create_logger(base_config))

    scheduler_service = build_runtime_services(base_config)
    if args.dump_runtime:
        print(json.dumps(scheduler_service.dump_runtime(), indent=2, ensure_ascii=False))
        return 0
    if args.list_slots:
        print(json.dumps(scheduler_service.list_slots(), indent=2, ensure_ascii=False))
        return 0
    if args.list_scenarios:
        print(json.dumps(scheduler_service.list_scenarios(), indent=2, ensure_ascii=False))
        return 0
    if args.history:
        print(
            json.dumps(
                scheduler_service.read_history(
                    limit=args.history_limit,
                    status=args.history_status,
                    slot_id=args.history_slot_id,
                    scenario_id=args.history_scenario_id,
                    execution_id=args.history_execution_id,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    if args.prune_history_days is not None:
        removed = scheduler_service.prune_history(older_than_days=args.prune_history_days)
        print(json.dumps({"removed": removed, "older_than_days": args.prune_history_days}, indent=2, ensure_ascii=False))
        return 0
    if args.plan:
        print(json.dumps(scheduler_service.describe_plan(), indent=2, ensure_ascii=False))
        return 0
    if args.export_plan:
        plan = scheduler_service.describe_plan()
        export_path = Path(args.export_plan)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with export_path.open("w", encoding="utf-8") as handle:
            json.dump(plan, handle, indent=2, ensure_ascii=False)
        return 0
    if args.run_slot:
        return scheduler_service.run_slot(args.run_slot, dry_run=args.dry_run)
    if args.run_scenario:
        return scheduler_service.run_scenario(args.run_scenario, dry_run=args.dry_run)
    if args.run_next:
        return scheduler_service.run_next(dry_run=args.dry_run)
    if args.check:
        return scheduler_service.run_check_mode(dry_run=args.dry_run)
    return scheduler_service.loop(dry_run=args.dry_run, once=args.once)


if __name__ == "__main__":
    sys.exit(main())
