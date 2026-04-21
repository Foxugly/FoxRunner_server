from __future__ import annotations

from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait

from .registry import OperationContext


def handle_open_url(context: OperationContext, payload: dict) -> None:
    if context.dry_run:
        return
    if context.driver is None:
        raise RuntimeError("Driver Selenium indisponible pour open_url.")
    if "url" not in payload:
        raise ValueError("open_url exige 'url'.")
    context.driver.get(str(payload["url"]))


def handle_click(context: OperationContext, payload: dict) -> None:
    if context.dry_run:
        return
    if context.driver is None:
        raise RuntimeError("Driver Selenium indisponible pour click.")
    element = WebDriverWait(context.driver, int(payload.get("timeout", 30))).until(EC.element_to_be_clickable((_resolve_by(payload["by"]), payload["locator"])))
    context.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    element.click()


def handle_wait_for_element(context: OperationContext, payload: dict) -> None:
    if context.dry_run:
        return
    if context.driver is None:
        raise RuntimeError("Driver Selenium indisponible pour wait_for_element.")
    WebDriverWait(context.driver, int(payload.get("timeout", 30))).until(EC.presence_of_element_located((_resolve_by(payload["by"]), payload["locator"])))


def handle_input_text(context: OperationContext, payload: dict) -> None:
    if context.dry_run:
        return
    if context.driver is None:
        raise RuntimeError("Driver Selenium indisponible pour input_text.")
    element = WebDriverWait(context.driver, int(payload.get("timeout", 30))).until(EC.presence_of_element_located((_resolve_by(payload["by"]), payload["locator"])))
    if payload.get("clear_first", True):
        element.clear()
    element.send_keys(str(payload["text"]))


def handle_assert_text(context: OperationContext, payload: dict) -> None:
    if context.dry_run:
        return
    if context.driver is None:
        raise RuntimeError("Driver Selenium indisponible pour assert_text.")
    element = WebDriverWait(context.driver, int(payload.get("timeout", 30))).until(EC.presence_of_element_located((_resolve_by(payload["by"]), payload["locator"])))
    actual_text = element.text or ""
    expected_text = str(payload["text"])
    mode = payload.get("match", "contains")
    if mode == "equals" and actual_text != expected_text:
        raise AssertionError(f"Texte exact attendu '{expected_text}', obtenu '{actual_text}'.")
    if mode == "contains" and expected_text not in actual_text:
        raise AssertionError(f"Texte contenant '{expected_text}' attendu, obtenu '{actual_text}'.")


def handle_assert_attribute(context: OperationContext, payload: dict) -> None:
    if context.dry_run:
        return
    if context.driver is None:
        raise RuntimeError("Driver Selenium indisponible pour assert_attribute.")
    element = WebDriverWait(context.driver, int(payload.get("timeout", 30))).until(EC.presence_of_element_located((_resolve_by(payload["by"]), payload["locator"])))
    actual_value = element.get_attribute(str(payload["attribute"])) or ""
    expected_value = str(payload["value"])
    mode = payload.get("match", "contains")
    if mode == "equals" and actual_value != expected_value:
        raise AssertionError(f"Attribut exact attendu '{expected_value}', obtenu '{actual_value}'.")
    if mode == "contains" and expected_value not in actual_value:
        raise AssertionError(f"Attribut contenant '{expected_value}' attendu, obtenu '{actual_value}'.")


def handle_extract_text_to_context(context: OperationContext, payload: dict) -> None:
    target = payload["target"]
    if context.dry_run:
        context.template_context[target.key] = "<dry-run>"
        return
    if context.driver is None:
        raise RuntimeError("Driver Selenium indisponible pour extract_text_to_context.")
    element = WebDriverWait(context.driver, target.timeout).until(EC.presence_of_element_located((_resolve_by(target.by), target.locator)))
    context.template_context[target.key] = element.text or ""


def handle_extract_attribute_to_context(context: OperationContext, payload: dict) -> None:
    target = payload["target"]
    if context.dry_run:
        context.template_context[target.key] = "<dry-run>"
        return
    if context.driver is None:
        raise RuntimeError("Driver Selenium indisponible pour extract_attribute_to_context.")
    element = WebDriverWait(context.driver, target.timeout).until(EC.presence_of_element_located((_resolve_by(target.by), target.locator)))
    context.template_context[target.key] = element.get_attribute(target.attribute or "") or ""


def handle_screenshot(context: OperationContext, payload: dict) -> None:
    if context.dry_run:
        return
    if context.driver is None:
        raise RuntimeError("Driver Selenium indisponible pour screenshot.")
    path = Path(str(payload["path"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    context.driver.save_screenshot(str(path))


def handle_select_option(context: OperationContext, payload: dict) -> None:
    if context.dry_run:
        return
    if context.driver is None:
        raise RuntimeError("Driver Selenium indisponible pour select_option.")
    element = WebDriverWait(context.driver, int(payload.get("timeout", 30))).until(EC.presence_of_element_located((_resolve_by(payload["by"]), payload["locator"])))
    select = Select(element)
    if "value" in payload:
        select.select_by_value(str(payload["value"]))
        return
    if "visible_text" in payload:
        select.select_by_visible_text(str(payload["visible_text"]))
        return
    if "index" in payload:
        select.select_by_index(int(payload["index"]))
        return
    raise ValueError("select_option exige 'value', 'visible_text' ou 'index'.")


def handle_wait_until_url_contains(context: OperationContext, payload: dict) -> None:
    if context.dry_run:
        return
    if context.driver is None:
        raise RuntimeError("Driver Selenium indisponible pour wait_until_url_contains.")
    WebDriverWait(context.driver, int(payload.get("timeout", 30))).until(EC.url_contains(str(payload["value"])))


def handle_wait_until_title_contains(context: OperationContext, payload: dict) -> None:
    if context.dry_run:
        return
    if context.driver is None:
        raise RuntimeError("Driver Selenium indisponible pour wait_until_title_contains.")
    WebDriverWait(context.driver, int(payload.get("timeout", 30))).until(EC.title_contains(str(payload["value"])))


def handle_close_browser(context: OperationContext, payload: dict) -> None:
    if context.dry_run:
        return
    if context.driver is not None:
        context.driver.quit()
        context.driver = None


def _resolve_by(value: str) -> By:
    mapping = {
        "id": By.ID,
        "xpath": By.XPATH,
        "css": By.CSS_SELECTOR,
        "name": By.NAME,
        "class_name": By.CLASS_NAME,
        "tag_name": By.TAG_NAME,
        "link_text": By.LINK_TEXT,
        "partial_link_text": By.PARTIAL_LINK_TEXT,
    }
    try:
        return mapping[value.lower()]
    except KeyError as exc:
        raise ValueError(f"Strategie Selenium non supportee: {value}") from exc
