from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from selenium import webdriver

from app.config import TaskConfig
from app.logger import Logger
from app.notifier import Notifier
from operations import OperationContext, build_operation_registry, build_parallel_safe_steps
from scenarios.engine import EngineContext, execute_block_step, is_atomic_step
from scenarios.loader import ScenarioData, ScenarioDefinition, ScenarioStep


@dataclass(frozen=True)
class TaskRunResult:
    success: bool
    step: str
    message: str
    execution_id: str | None = None
    screenshot_path: str | None = None
    page_source_path: str | None = None


def create_driver(config: TaskConfig):
    options = webdriver.ChromeOptions()
    if config.headless:
        options.add_argument("--headless=new")
    options.add_argument(f"--window-size={config.browser_window_size}")
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(config.page_load_timeout_seconds)
    return driver


def run_task(
    config: TaskConfig,
    logger: Logger,
    scenario: ScenarioDefinition,
    scenario_data: ScenarioData,
    dry_run: bool = False,
    notifier: Notifier | None = None,
    network_check: Callable[[], bool] | None = None,
    network_check_by_key: Callable[[str | None], bool] | None = None,
    initial_context: dict[str, str] | None = None,
    artifacts_dir: Path | None = None,
) -> TaskRunResult:
    driver = None
    current_step = "dry_run" if dry_run else "driver_created"
    context: dict[str, str] = dict(initial_context or {})
    operation_registry = build_operation_registry()
    parallel_safe_steps = build_parallel_safe_steps()

    try:
        _execute_hook_steps(
            scenario.before_steps,
            operation_registry=operation_registry,
            driver_ref={"driver": driver},
            config=config,
            logger=logger,
            notifier=notifier,
            network_check=network_check,
            network_check_by_key=network_check_by_key,
            scenario_data=scenario_data,
            context=context,
            dry_run=dry_run,
            parallel_safe_steps=parallel_safe_steps,
        )
        driver = context.get("__driver__", driver)

        for index, step in enumerate(scenario.steps, start=1):
            current_step = f"{index}:{step.type}"
            if not dry_run and _requires_driver(step.type) and driver is None:
                driver = create_driver(config)
            driver = _execute_scenario_step(
                step,
                operation_registry=operation_registry,
                driver=driver,
                config=config,
                logger=logger,
                notifier=notifier,
                network_check=network_check,
                network_check_by_key=network_check_by_key,
                scenario_data=scenario_data,
                context=context,
                dry_run=dry_run,
                parallel_safe_steps=parallel_safe_steps,
            )

        context["current_step"] = current_step
        _execute_hook_steps(
            scenario.on_success_steps,
            operation_registry=operation_registry,
            driver_ref={"driver": driver},
            config=config,
            logger=logger,
            notifier=notifier,
            network_check=network_check,
            network_check_by_key=network_check_by_key,
            scenario_data=scenario_data,
            context=context,
            dry_run=dry_run,
            parallel_safe_steps=parallel_safe_steps,
        )
        driver = context.get("__driver__", driver)
        if dry_run:
            logger.success("Dry-run complet du scenario termine.")
            return TaskRunResult(True, current_step, "Dry-run complet du scenario execute.", execution_id=context.get("execution_id"))
        logger.success("Actions terminees.")
        return TaskRunResult(True, current_step, "Tache executee avec succes.", execution_id=context.get("execution_id"))
    except Exception as exc:
        context["current_step"] = current_step
        context["error_message"] = str(exc)
        screenshot_path = _capture_failure_screenshot(driver, context, logger, dry_run, artifacts_dir)
        page_source_path = _capture_failure_page_source(driver, context, logger, dry_run, artifacts_dir)
        _execute_hook_steps(
            scenario.on_failure_steps,
            operation_registry=operation_registry,
            driver_ref={"driver": driver},
            config=config,
            logger=logger,
            notifier=notifier,
            network_check=network_check,
            network_check_by_key=network_check_by_key,
            scenario_data=scenario_data,
            context=context,
            dry_run=dry_run,
            parallel_safe_steps=parallel_safe_steps,
        )
        driver = context.get("__driver__", driver)
        logger.error(f"Echec de la tache Selenium a l'etape {current_step}: {exc}")
        return TaskRunResult(
            False,
            current_step,
            str(exc),
            execution_id=context.get("execution_id"),
            screenshot_path=screenshot_path,
            page_source_path=page_source_path,
        )
    finally:
        context["current_step"] = current_step
        _execute_hook_steps(
            scenario.finally_steps,
            operation_registry=operation_registry,
            driver_ref={"driver": driver},
            config=config,
            logger=logger,
            notifier=notifier,
            network_check=network_check,
            network_check_by_key=network_check_by_key,
            scenario_data=scenario_data,
            context=context,
            dry_run=dry_run,
            parallel_safe_steps=parallel_safe_steps,
        )
        driver = context.get("__driver__", driver)
        if driver is not None:
            driver.quit()


def _execute_step(
    operation_registry,
    driver,
    step_type,
    payload,
    config,
    logger,
    notifier,
    network_check,
    network_check_by_key,
    scenario_data,
    context,
    parallel_safe_steps,
    dry_run=False,
):
    try:
        handler = operation_registry[step_type]
    except KeyError as exc:
        raise ValueError(f"Type d'etape non supporte: {step_type}") from exc

    op_context = OperationContext(
        driver=driver,
        config=config,
        logger=logger,
        notifier=notifier,
        network_check=network_check,
        network_check_by_key=network_check_by_key,
        template_context=context,
        pushovers=scenario_data.pushovers,
        default_pushover_key=scenario_data.default_pushover_key,
        networks=scenario_data.networks,
        default_network_key=scenario_data.default_network_key,
        parallel_safe_steps=parallel_safe_steps,
        dry_run=dry_run,
    )
    handler(op_context, payload)
    return op_context.driver


def _execute_scenario_step(
    step: ScenarioStep,
    *,
    operation_registry,
    driver,
    config,
    logger,
    notifier,
    network_check,
    network_check_by_key,
    scenario_data,
    context,
    dry_run: bool,
    parallel_safe_steps,
) -> None:
    if not _should_execute(step, context):
        return

    attempts = max(step.retry + 1, 1)
    last_error: Exception | None = None

    for attempt_index in range(attempts):
        try:
            if not is_atomic_step(step.type):
                return execute_block_step(
                    step,
                    EngineContext(
                        operation_registry=operation_registry,
                        execute_atomic_step=_execute_step,
                        execute_scenario_step=_execute_scenario_step,
                        parallel_safe_steps=parallel_safe_steps,
                        driver=driver,
                        config=config,
                        logger=logger,
                        notifier=notifier,
                        network_check=network_check,
                        network_check_by_key=network_check_by_key,
                        scenario_data=scenario_data,
                        context=context,
                        dry_run=dry_run,
                    ),
                )
            if not dry_run and _requires_driver(step.type) and driver is None:
                driver = create_driver(config)
            if step.timeout_seconds is not None:
                updated_driver = _run_with_timeout(
                    lambda: _execute_step(
                        operation_registry,
                        driver,
                        step.type,
                        step.payload,
                        config,
                        logger,
                        notifier,
                        network_check,
                        network_check_by_key,
                        scenario_data,
                        context,
                        parallel_safe_steps,
                        dry_run=dry_run,
                    ),
                    step.timeout_seconds,
                )
                driver = updated_driver
            else:
                driver = _execute_step(
                    operation_registry,
                    driver,
                    step.type,
                    step.payload,
                    config,
                    logger,
                    notifier,
                    network_check,
                    network_check_by_key,
                    scenario_data,
                    context,
                    parallel_safe_steps,
                    dry_run=dry_run,
                )
            return driver
        except Exception as exc:
            last_error = exc
            if attempt_index < attempts - 1 and step.retry_delay_seconds > 0:
                time.sleep(step.retry_delay_seconds * (step.retry_backoff_seconds**attempt_index))

    if last_error is not None:
        if step.continue_on_error:
            context["error_message"] = str(last_error)
            context["last_error_message"] = str(last_error)
            context["last_error_step"] = step.type
            logger.warning(f"Etape ignoree apres erreur ({step.type}): {last_error}")
            return driver
        raise last_error
    return driver


def _should_execute(step: ScenarioStep, context: dict[str, str]) -> bool:
    if step.when is None:
        return True
    expression = step.when
    if expression.startswith("context_exists:"):
        key = expression.split(":", 1)[1]
        return bool(context.get(key))
    if expression.startswith("context_not_exists:"):
        key = expression.split(":", 1)[1]
        return not bool(context.get(key))
    if expression.startswith("context_equals:"):
        raw = expression.split(":", 1)[1]
        key, expected = raw.split("=", 1)
        return str(context.get(key, "")) == expected
    if expression.startswith("context_in:"):
        raw = expression.split(":", 1)[1]
        key, values = raw.split("=", 1)
        expected_values = {item.strip() for item in values.split(",") if item.strip()}
        return str(context.get(key, "")) in expected_values
    if expression.startswith("context_matches:"):
        import re

        raw = expression.split(":", 1)[1]
        key, pattern = raw.split("=", 1)
        return re.search(pattern, str(context.get(key, ""))) is not None
    raise ValueError(f"Condition DSL non supportee: {expression}")


def _run_with_timeout(fn, timeout_seconds: float) -> None:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            raise TimeoutError(f"Etape depassee apres {timeout_seconds}s.") from exc


def _requires_driver(step_type: str) -> bool:
    return step_type in {
        "open_url",
        "click",
        "wait_for_element",
        "input_text",
        "assert_text",
        "assert_attribute",
        "extract_text_to_context",
        "extract_attribute_to_context",
        "screenshot",
        "select_option",
        "wait_until_url_contains",
        "wait_until_title_contains",
        "close_browser",
    }


def _execute_hook_steps(
    steps,
    *,
    operation_registry,
    driver_ref,
    config: TaskConfig,
    logger: Logger,
    notifier: Notifier | None,
    network_check: Callable[[], bool] | None,
    network_check_by_key: Callable[[str | None], bool] | None,
    scenario_data: ScenarioData,
    context: dict[str, str],
    dry_run: bool,
    parallel_safe_steps,
) -> None:
    for step in steps:
        driver_ref["driver"] = _execute_scenario_step(
            step,
            operation_registry=operation_registry,
            driver=driver_ref["driver"],
            config=config,
            logger=logger,
            notifier=notifier,
            network_check=network_check,
            network_check_by_key=network_check_by_key,
            scenario_data=scenario_data,
            context=context,
            dry_run=dry_run,
            parallel_safe_steps=parallel_safe_steps,
        )
    # Only propagate the driver when hooks actually produced one; otherwise we
    # would clobber the driver already held by run_task and leak the original.
    if driver_ref["driver"] is not None:
        context["__driver__"] = driver_ref["driver"]


def _capture_failure_screenshot(driver, context: dict[str, str], logger: Logger, dry_run: bool, artifacts_dir: Path | None) -> str | None:
    if dry_run or driver is None:
        return None
    try:
        execution_id = context.get("execution_id", "manual")
        screenshot_dir = (artifacts_dir or Path(".runtime") / "artifacts") / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshot_dir / f"{execution_id}.png"
        driver.save_screenshot(str(screenshot_path))
        logger.warning(f"Screenshot erreur enregistre: {screenshot_path}")
        return str(screenshot_path)
    except Exception as exc:
        logger.debug(f"Impossible de capturer le screenshot d'erreur: {exc}")
        return None


def _capture_failure_page_source(driver, context: dict[str, str], logger: Logger, dry_run: bool, artifacts_dir: Path | None) -> str | None:
    if dry_run or driver is None:
        return None
    try:
        execution_id = context.get("execution_id", "manual")
        page_source_dir = (artifacts_dir or Path(".runtime") / "artifacts") / "pages"
        page_source_dir.mkdir(parents=True, exist_ok=True)
        page_source_path = page_source_dir / f"{execution_id}.html"
        page_source_path.write_text(driver.page_source, encoding="utf-8")
        logger.warning(f"Page source erreur enregistree: {page_source_path}")
        return str(page_source_path)
    except Exception as exc:
        logger.debug(f"Impossible d'enregistrer le page source d'erreur: {exc}")
        return None
