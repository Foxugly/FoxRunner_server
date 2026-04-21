from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass

from scenarios.loader import ScenarioStep
from scenarios.schema import ATOMIC_STEP_TYPES


@dataclass
class EngineContext:
    operation_registry: dict
    execute_atomic_step: callable
    execute_scenario_step: callable
    parallel_safe_steps: frozenset[str]
    driver: object
    config: object
    logger: object
    notifier: object
    network_check: object
    network_check_by_key: object
    scenario_data: object
    context: dict[str, str]
    dry_run: bool


def is_atomic_step(step_type: str) -> bool:
    return step_type in ATOMIC_STEP_TYPES


def execute_block_step(step: ScenarioStep, engine: EngineContext):
    if step.type == "group":
        return execute_steps_sequence(step.payload["steps"], engine)
    if step.type == "repeat":
        updated_driver = engine.driver
        for _ in range(int(step.payload["times"])):
            updated_driver = execute_steps_sequence(step.payload["steps"], _replace_driver(engine, updated_driver))
        return updated_driver
    if step.type == "parallel":
        return execute_parallel_steps(step.payload["steps"], engine)
    if step.type == "try":
        return execute_try_step(step, engine)
    raise ValueError(f"Bloc DSL non supporte: {step.type}")


def execute_steps_sequence(steps, engine: EngineContext):
    updated_driver = engine.driver
    for child in steps:
        updated_driver = engine.execute_scenario_step(
            child,
            operation_registry=engine.operation_registry,
            driver=updated_driver,
            config=engine.config,
            logger=engine.logger,
            notifier=engine.notifier,
            network_check=engine.network_check,
            network_check_by_key=engine.network_check_by_key,
            scenario_data=engine.scenario_data,
            context=engine.context,
            dry_run=engine.dry_run,
            parallel_safe_steps=engine.parallel_safe_steps,
        )
    return updated_driver


def execute_parallel_steps(steps, engine: EngineContext):
    unsupported = [child.type for child in steps if child.type not in engine.parallel_safe_steps]
    if unsupported:
        raise ValueError(f"Le bloc 'parallel' ne supporte pas ces types: {', '.join(sorted(set(unsupported)))}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(steps) or 1) as executor:
        futures = [
            executor.submit(
                engine.execute_scenario_step,
                child,
                operation_registry=engine.operation_registry,
                driver=None,
                config=engine.config,
                logger=engine.logger,
                notifier=engine.notifier,
                network_check=engine.network_check,
                network_check_by_key=engine.network_check_by_key,
                scenario_data=engine.scenario_data,
                context=dict(engine.context),
                dry_run=engine.dry_run,
                parallel_safe_steps=engine.parallel_safe_steps,
            )
            for child in steps
        ]
        for future in futures:
            future.result()
    return engine.driver


def execute_try_step(step: ScenarioStep, engine: EngineContext):
    updated_driver = engine.driver
    try:
        updated_driver = execute_steps_sequence(step.payload["try_steps"], _replace_driver(engine, updated_driver))
    except Exception as exc:
        engine.context["error_message"] = str(exc)
        if step.payload["catch_steps"]:
            updated_driver = execute_steps_sequence(step.payload["catch_steps"], _replace_driver(engine, updated_driver))
        else:
            raise
    finally:
        if step.payload["finally_steps"]:
            updated_driver = execute_steps_sequence(step.payload["finally_steps"], _replace_driver(engine, updated_driver))
    return updated_driver


def _replace_driver(engine: EngineContext, driver, context: dict[str, str] | None = None) -> EngineContext:
    return EngineContext(
        operation_registry=engine.operation_registry,
        execute_atomic_step=engine.execute_atomic_step,
        execute_scenario_step=engine.execute_scenario_step,
        parallel_safe_steps=engine.parallel_safe_steps,
        driver=driver,
        config=engine.config,
        logger=engine.logger,
        notifier=engine.notifier,
        network_check=engine.network_check,
        network_check_by_key=engine.network_check_by_key,
        scenario_data=engine.scenario_data,
        context=engine.context if context is None else context,
        dry_run=engine.dry_run,
    )
