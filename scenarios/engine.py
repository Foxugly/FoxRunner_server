from __future__ import annotations

import concurrent.futures
import contextlib
import time
import traceback
from dataclasses import dataclass

from scenarios.events import StepEvent, StepEventSink
from scenarios.loader import ScenarioStep
from scenarios.schema import ATOMIC_STEP_TYPES


def _emit(on_event: StepEventSink | None, **kwargs) -> None:
    """Best-effort sink call — a sink error must never break the run."""
    if on_event is None:
        return
    with contextlib.suppress(Exception):
        on_event(StepEvent(**kwargs))


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
    on_event: StepEventSink | None = None
    step_id: str | None = None


def is_atomic_step(step_type: str) -> bool:
    return step_type in ATOMIC_STEP_TYPES


def execute_block_step(step: ScenarioStep, engine: EngineContext):
    if step.type == "group":
        return execute_steps_sequence(step.payload["steps"], engine, "group")
    if step.type == "repeat":
        updated_driver = engine.driver
        for _ in range(int(step.payload["times"])):
            updated_driver = execute_steps_sequence(step.payload["steps"], _replace_driver(engine, updated_driver), "repeat")
        return updated_driver
    if step.type == "parallel":
        return execute_parallel_steps(step.payload["steps"], engine)
    if step.type == "try":
        return execute_try_step(step, engine)
    raise ValueError(f"Bloc DSL non supporte: {step.type}")


def _child_step_id(engine: EngineContext, block_label: str, index: int) -> str | None:
    """Build a nested step id like ``steps[2]>group[0]`` (best-effort).

    Returns ``None`` when the parent step id is unknown so the child call
    falls back to the engine's default behaviour.
    """
    if engine.step_id is None:
        return None
    return f"{engine.step_id}>{block_label}[{index}]"


def execute_steps_sequence(steps, engine: EngineContext, block_label: str = "block"):
    updated_driver = engine.driver
    for index, child in enumerate(steps):
        child_id = _child_step_id(engine, block_label, index)
        _emit(engine.on_event, step_id=child_id or "", event_type="step_started", step_type=child.type)
        started = time.monotonic()
        engine.context.pop("__step_swallowed_error__", None)
        try:
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
                on_event=engine.on_event,
                step_id=child_id,
            )
        except Exception as exc:
            _emit(
                engine.on_event,
                step_id=child_id or "",
                event_type="step_failed",
                step_type=child.type,
                level="error",
                message=str(exc),
                traceback=traceback.format_exc(),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            raise
        # A continue_on_error child already emitted a warning step_failed; do
        # not also render it green.
        if engine.context.pop("__step_swallowed_error__", None) is None:
            _emit(
                engine.on_event,
                step_id=child_id or "",
                event_type="step_succeeded",
                step_type=child.type,
                duration_ms=int((time.monotonic() - started) * 1000),
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
                on_event=engine.on_event,
                step_id=_child_step_id(engine, "parallel", index),
            )
            for index, child in enumerate(steps)
        ]
        for future in futures:
            future.result()
    return engine.driver


def execute_try_step(step: ScenarioStep, engine: EngineContext):
    updated_driver = engine.driver
    try:
        updated_driver = execute_steps_sequence(step.payload["try_steps"], _replace_driver(engine, updated_driver), "try")
    except Exception as exc:
        engine.context["error_message"] = str(exc)
        if step.payload["catch_steps"]:
            updated_driver = execute_steps_sequence(step.payload["catch_steps"], _replace_driver(engine, updated_driver), "catch")
        else:
            raise
    finally:
        if step.payload["finally_steps"]:
            updated_driver = execute_steps_sequence(step.payload["finally_steps"], _replace_driver(engine, updated_driver), "finally")
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
        on_event=engine.on_event,
        step_id=engine.step_id,
    )
