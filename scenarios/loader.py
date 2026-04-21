from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import NetworkConfig, PushoverConfig
from scenarios.schema import DSL_SCHEMA_VERSION, SUPPORTED_STEP_TYPES
from scenarios.validator import validate_json_document
from scheduler.model import TimeSlot

SLOTS_SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schemas" / "slots.schema.json"
SCENARIOS_SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schemas" / "scenarios.schema.json"


@dataclass(frozen=True)
class ScenarioStep:
    type: str
    payload: dict[str, Any]
    when: str | None = None
    retry: int = 0
    retry_delay_seconds: float = 0.0
    retry_backoff_seconds: float = 1.0
    timeout_seconds: float | None = None
    continue_on_error: bool = False


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: str
    description: str
    steps: tuple[ScenarioStep, ...]
    before_steps: tuple[ScenarioStep, ...] = ()
    on_success_steps: tuple[ScenarioStep, ...] = ()
    on_failure_steps: tuple[ScenarioStep, ...] = ()
    finally_steps: tuple[ScenarioStep, ...] = ()
    requires_enterprise_network: bool = False


@dataclass(frozen=True)
class ScenarioData:
    pushovers: dict[str, PushoverConfig]
    networks: dict[str, NetworkConfig]
    default_pushover_key: str | None = None
    default_network_key: str | None = None


@dataclass(frozen=True)
class StepReference:
    pushover: str | None = None
    network: str | None = None


@dataclass(frozen=True)
class ExtractTarget:
    key: str
    by: str
    locator: str
    timeout: int = 30
    attribute: str | None = None


def load_slots(path: Path) -> tuple[TimeSlot, ...]:
    data = _load_json(path)
    validate_slots_document(data, path.name)
    _require_keys(data, ("slots",), f"{path.name} root")
    if not isinstance(data["slots"], list):
        raise ValueError(f"{path.name}: 'slots' doit etre une liste.")
    return build_slots_from_items(data.get("slots", []), path.name)


def build_slots_from_items(items: list[dict[str, Any]], source_name: str = "slots") -> tuple[TimeSlot, ...]:
    slots: list[TimeSlot] = []
    for item in items:
        _require_keys(item, ("id", "days", "start", "end", "scenario"), f"{source_name} slot")
        if not isinstance(item["days"], list) or not all(isinstance(day, int) for day in item["days"]):
            raise ValueError(f"{source_name}: 'days' doit etre une liste d'entiers.")
        start_hour, start_minute = _parse_hhmm(item["start"])
        end_hour, end_minute = _parse_hhmm(item["end"])
        slots.append(
            TimeSlot(
                slot_id=item["id"],
                days=tuple(item["days"]),
                start_hour=start_hour,
                start_minute=start_minute,
                end_hour=end_hour,
                end_minute=end_minute,
                scenario_id=item["scenario"],
            )
        )
    return tuple(slots)


def load_scenarios(path: Path) -> dict[str, ScenarioDefinition]:
    data = _load_json(path)
    validate_scenarios_document(data, path.name)
    return build_scenarios_from_map(data.get("scenarios", {}), path.name)


def build_scenarios_from_map(items: dict[str, dict[str, Any]], source_name: str = "scenarios") -> dict[str, ScenarioDefinition]:
    scenarios: dict[str, ScenarioDefinition] = {}
    for scenario_id, item in items.items():
        if not isinstance(item, dict):
            raise ValueError(f"{source_name}: scenario '{scenario_id}' invalide.")
        _require_keys(item, ("steps",), f"{source_name} scenario '{scenario_id}'")
        before_steps = tuple(_build_step(step, source_name, scenario_id) for step in item.get("before_steps", []))
        steps = tuple(_build_step(step, source_name, scenario_id) for step in item.get("steps", []))
        on_success_steps = tuple(_build_step(step, source_name, scenario_id) for step in item.get("on_success", []))
        on_failure_steps = tuple(_build_step(step, source_name, scenario_id) for step in item.get("on_failure", []))
        finally_steps = tuple(_build_step(step, source_name, scenario_id) for step in item.get("finally_steps", []))
        scenarios[scenario_id] = ScenarioDefinition(
            scenario_id=scenario_id,
            description=item.get("description", ""),
            before_steps=before_steps,
            steps=steps,
            on_success_steps=on_success_steps,
            on_failure_steps=on_failure_steps,
            finally_steps=finally_steps,
            requires_enterprise_network=_scenario_requires_enterprise_network(
                before_steps,
                steps,
                on_success_steps,
                on_failure_steps,
                finally_steps,
            ),
        )
    return scenarios


def load_scenario_data(path: Path) -> ScenarioData:
    data = _load_json(path)
    validate_scenarios_document(data, path.name)
    raw_data = data["data"]
    if not isinstance(raw_data, dict):
        raise ValueError(f"{path.name}: 'data' doit etre un objet.")

    pushovers = _load_pushover_map(raw_data, path.name)
    networks = _load_network_map(raw_data, path.name)
    default_pushover_key = _resolve_default_key(raw_data.get("default_pushover"), pushovers, "pushovers", path.name)
    default_network_key = _resolve_default_key(raw_data.get("default_network"), networks, "networks", path.name)
    return ScenarioData(
        pushovers=pushovers,
        networks=networks,
        default_pushover_key=default_pushover_key,
        default_network_key=default_network_key,
    )


def load_pushover_from_scenarios(path: Path) -> PushoverConfig | None:
    scenario_data = load_scenario_data(path)
    if scenario_data.default_pushover_key is None:
        return None
    return scenario_data.pushovers[scenario_data.default_pushover_key]


def load_network_config_from_scenarios(path: Path) -> NetworkConfig:
    scenario_data = load_scenario_data(path)
    if scenario_data.default_network_key is None:
        raise ValueError(f"{path.name}: aucune configuration reseau par defaut definie dans 'data.default_network'.")
    return scenario_data.networks[scenario_data.default_network_key]


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour_str, minute_str = value.split(":", 1)
    return int(hour_str), int(minute_str)


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: le document JSON racine doit etre un objet.")
    return data


def _require_keys(data: dict, keys: tuple[str, ...], context: str) -> None:
    missing = [key for key in keys if key not in data]
    if missing:
        raise ValueError(f"{context}: cles manquantes: {', '.join(missing)}")


def _build_step(step: dict, filename: str, scenario_id: str) -> ScenarioStep:
    if not isinstance(step, dict):
        raise ValueError(f"{filename}: scenario '{scenario_id}' contient une etape invalide.")
    _require_keys(step, ("type",), f"{filename} scenario '{scenario_id}' step")
    step_type = step["type"]
    if step_type not in SUPPORTED_STEP_TYPES:
        raise ValueError(f"{filename}: type d'etape non supporte: {step_type}")
    payload = _build_step_payload(step_type, step, filename, scenario_id)
    retry = int(step.get("retry", 0))
    retry_delay_seconds = float(step.get("retry_delay_seconds", 0))
    retry_backoff_seconds = float(step.get("retry_backoff_seconds", 1.0))
    if retry < 0:
        raise ValueError(f"{filename}: 'retry' doit etre >= 0.")
    if retry_delay_seconds < 0:
        raise ValueError(f"{filename}: 'retry_delay_seconds' doit etre >= 0.")
    if retry_backoff_seconds < 1.0:
        raise ValueError(f"{filename}: 'retry_backoff_seconds' doit etre >= 1.0.")
    timeout_seconds = step.get("timeout_seconds")
    if timeout_seconds is not None:
        timeout_seconds = float(timeout_seconds)
    when = step.get("when")
    if when is not None and not isinstance(when, str):
        raise ValueError(f"{filename}: 'when' doit etre une chaine.")
    continue_on_error = bool(step.get("continue_on_error", False))
    return ScenarioStep(
        type=step_type,
        payload=payload,
        when=when,
        retry=retry,
        retry_delay_seconds=retry_delay_seconds,
        retry_backoff_seconds=retry_backoff_seconds,
        timeout_seconds=timeout_seconds,
        continue_on_error=continue_on_error,
    )


def _build_step_payload(step_type: str, step: dict, filename: str, scenario_id: str) -> dict[str, Any]:
    payload = {k: v for k, v in step.items() if k not in {"type", "when", "retry", "retry_delay_seconds", "retry_backoff_seconds", "timeout_seconds", "continue_on_error"}}
    if step_type in {"group", "parallel", "repeat"}:
        _require_keys(payload, ("steps",), f"{filename} scenario '{scenario_id}' step '{step_type}'")
        payload["steps"] = tuple(_build_step(child, filename, scenario_id) for child in payload["steps"])
    if step_type == "repeat":
        _require_keys(payload, ("times",), f"{filename} scenario '{scenario_id}' step 'repeat'")
        payload["times"] = int(payload["times"])
    if step_type == "try":
        _require_keys(payload, ("try_steps",), f"{filename} scenario '{scenario_id}' step 'try'")
        payload["try_steps"] = tuple(_build_step(child, filename, scenario_id) for child in payload["try_steps"])
        payload["catch_steps"] = tuple(_build_step(child, filename, scenario_id) for child in payload.get("catch_steps", []))
        payload["finally_steps"] = tuple(_build_step(child, filename, scenario_id) for child in payload.get("finally_steps", []))
    if "ref" in payload and isinstance(payload["ref"], dict):
        payload["ref"] = StepReference(
            pushover=payload["ref"].get("pushover"),
            network=payload["ref"].get("network"),
        )
    if step_type in {"extract_text_to_context", "extract_attribute_to_context"}:
        payload = _build_extract_target(payload)
    _validate_step_payload(step_type, payload, filename, scenario_id)
    return payload


def _load_pushover_map(raw_data: dict, filename: str) -> dict[str, PushoverConfig]:
    raw_pushovers = raw_data.get("pushovers", {})
    if not isinstance(raw_pushovers, dict):
        raise ValueError(f"{filename}: 'data.pushovers' doit etre un objet.")

    pushovers: dict[str, PushoverConfig] = {}
    for key, item in raw_pushovers.items():
        if not isinstance(item, dict):
            raise ValueError(f"{filename}: 'data.pushovers.{key}' doit etre un objet.")
        token = item.get("token")
        user_key = item.get("user_key")
        if not token or not user_key:
            raise ValueError(f"{filename}: 'data.pushovers.{key}' doit contenir 'token' et 'user_key'.")
        pushovers[key] = PushoverConfig(
            token=token,
            user_key=user_key,
            sound=item.get("sound", "vibrate"),
            timeout_seconds=float(item.get("timeout_seconds", 20)),
        )
    return pushovers


def _load_network_map(raw_data: dict, filename: str) -> dict[str, NetworkConfig]:
    raw_networks = raw_data.get("networks", {})
    if not isinstance(raw_networks, dict):
        raise ValueError(f"{filename}: 'data.networks' doit etre un objet.")

    networks: dict[str, NetworkConfig] = {}
    for key, network in raw_networks.items():
        if not isinstance(network, dict):
            raise ValueError(f"{filename}: 'data.networks.{key}' doit etre un objet.")
        _require_keys(
            network,
            (
                "office_ipv4_networks",
                "office_gateway_networks",
                "office_dns_suffixes",
                "vpn_interface_keywords",
                "vpn_process_names",
                "internal_test_hosts",
                "internal_test_ports",
                "tcp_timeout_seconds",
                "home_like_networks",
                "allow_private_non_home_heuristic_for_vpn",
            ),
            f"{filename} data.networks.{key}",
        )
        networks[key] = NetworkConfig(
            office_ipv4_networks=tuple(_ensure_str_list(network["office_ipv4_networks"], filename, f"data.networks.{key}.office_ipv4_networks")),
            office_gateway_networks=tuple(_ensure_str_list(network["office_gateway_networks"], filename, f"data.networks.{key}.office_gateway_networks")),
            office_dns_suffixes=tuple(_ensure_str_list(network["office_dns_suffixes"], filename, f"data.networks.{key}.office_dns_suffixes")),
            vpn_interface_keywords=tuple(_ensure_str_list(network["vpn_interface_keywords"], filename, f"data.networks.{key}.vpn_interface_keywords")),
            vpn_process_names=tuple(_ensure_str_list(network["vpn_process_names"], filename, f"data.networks.{key}.vpn_process_names")),
            internal_test_hosts=tuple(_ensure_str_list(network["internal_test_hosts"], filename, f"data.networks.{key}.internal_test_hosts")),
            internal_test_ports=tuple(_ensure_int_list(network["internal_test_ports"], filename, f"data.networks.{key}.internal_test_ports")),
            tcp_timeout_seconds=float(network["tcp_timeout_seconds"]),
            home_like_networks=tuple(_ensure_str_list(network["home_like_networks"], filename, f"data.networks.{key}.home_like_networks")),
            allow_private_non_home_heuristic_for_vpn=bool(network["allow_private_non_home_heuristic_for_vpn"]),
        )
    return networks


def _resolve_default_key(raw_key, values: dict[str, object], field_name: str, filename: str) -> str | None:
    if raw_key is None:
        if not values:
            return None
        return "default" if "default" in values else next(iter(values))
    if not isinstance(raw_key, str):
        raise ValueError(f"{filename}: 'data.{field_name}' par defaut doit etre une chaine.")
    if raw_key not in values:
        raise ValueError(f"{filename}: 'data.{field_name}' ne contient pas la cle par defaut '{raw_key}'.")
    return raw_key


def _ensure_str_list(value, filename: str, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{filename}: '{field}' doit etre une liste de chaines.")
    return value


def _ensure_int_list(value, filename: str, field: str) -> list[int]:
    if not isinstance(value, list) or not all(isinstance(item, int) for item in value):
        raise ValueError(f"{filename}: '{field}' doit etre une liste d'entiers.")
    return value


def _scenario_requires_enterprise_network(*step_groups: tuple[ScenarioStep, ...]) -> bool:
    return any(_step_requires_enterprise_network(step) for steps in step_groups for step in steps)


def _step_requires_enterprise_network(step: ScenarioStep) -> bool:
    if step.type == "require_enterprise_network":
        return True
    if step.type in {"group", "parallel", "repeat"}:
        return any(_step_requires_enterprise_network(child) for child in step.payload["steps"])
    if step.type == "try":
        return any(_step_requires_enterprise_network(child) for key in ("try_steps", "catch_steps", "finally_steps") for child in step.payload.get(key, ()))
    return False


def validate_slots_document(data: dict[str, Any], filename: str) -> None:
    validate_json_document(data, SLOTS_SCHEMA_FILE, filename)
    _require_keys(data, ("slots",), f"{filename} root")
    if not isinstance(data["slots"], list):
        raise ValueError(f"{filename}: 'slots' doit etre une liste.")


def validate_scenarios_document(data: dict[str, Any], filename: str) -> None:
    validate_json_document(data, SCENARIOS_SCHEMA_FILE, filename)
    _require_keys(data, ("schema_version", "data", "scenarios"), f"{filename} root")
    if int(data["schema_version"]) != DSL_SCHEMA_VERSION:
        raise ValueError(f"{filename}: schema_version={data['schema_version']} non supporte, attendu {DSL_SCHEMA_VERSION}.")
    if not isinstance(data["data"], dict):
        raise ValueError(f"{filename}: 'data' doit etre un objet.")
    if not isinstance(data["scenarios"], dict):
        raise ValueError(f"{filename}: 'scenarios' doit etre un objet.")
    for scenario_id, item in data["scenarios"].items():
        if not isinstance(item, dict):
            raise ValueError(f"{filename}: scenario '{scenario_id}' invalide.")
        _require_keys(item, ("steps",), f"{filename} scenario '{scenario_id}'")
        for key in ("before_steps", "steps", "on_success", "on_failure", "finally_steps"):
            if key in item and not isinstance(item[key], list):
                raise ValueError(f"{filename}: '{key}' du scenario '{scenario_id}' doit etre une liste.")
        for key in ("before_steps", "steps", "on_success", "on_failure", "finally_steps"):
            for step in item.get(key, []):
                _validate_step_document(step, filename, scenario_id)


def _validate_step_document(step: dict[str, Any], filename: str, scenario_id: str) -> None:
    if not isinstance(step, dict):
        raise ValueError(f"{filename}: scenario '{scenario_id}' contient une etape invalide.")
    _require_keys(step, ("type",), f"{filename} scenario '{scenario_id}' step")
    step_type = step["type"]
    if step_type not in SUPPORTED_STEP_TYPES:
        raise ValueError(f"{filename}: type d'etape non supporte: {step_type}")
    if step_type in {"group", "parallel", "repeat"}:
        _require_keys(step, ("steps",), f"{filename} scenario '{scenario_id}' step '{step_type}'")
        if not isinstance(step["steps"], list):
            raise ValueError(f"{filename}: '{step_type}.steps' doit etre une liste.")
        for child in step["steps"]:
            _validate_step_document(child, filename, scenario_id)
    if step_type == "repeat":
        _require_keys(step, ("times",), f"{filename} scenario '{scenario_id}' step 'repeat'")
    if step_type == "try":
        _require_keys(step, ("try_steps",), f"{filename} scenario '{scenario_id}' step 'try'")
        for key in ("try_steps", "catch_steps", "finally_steps"):
            if key in step:
                if not isinstance(step[key], list):
                    raise ValueError(f"{filename}: '{key}' doit etre une liste.")
                for child in step[key]:
                    _validate_step_document(child, filename, scenario_id)
    _validate_step_payload_document(step_type, step, filename, scenario_id)


def _validate_step_payload_document(step_type: str, step: dict[str, Any], filename: str, scenario_id: str) -> None:
    if step_type == "open_url":
        _require_keys(step, ("url",), f"{filename} scenario '{scenario_id}' step 'open_url'")
    if step_type in {"click", "wait_for_element"}:
        _require_keys(step, ("by", "locator"), f"{filename} scenario '{scenario_id}' step '{step_type}'")
    if step_type == "input_text":
        _require_keys(step, ("by", "locator", "text"), f"{filename} scenario '{scenario_id}' step 'input_text'")
    if step_type == "assert_text":
        _require_keys(step, ("by", "locator", "text"), f"{filename} scenario '{scenario_id}' step 'assert_text'")
    if step_type == "assert_attribute":
        _require_keys(step, ("by", "locator", "attribute", "value"), f"{filename} scenario '{scenario_id}' step 'assert_attribute'")
    if step_type == "extract_text_to_context":
        _require_keys(step, ("key", "by", "locator"), f"{filename} scenario '{scenario_id}' step 'extract_text_to_context'")
    if step_type == "extract_attribute_to_context":
        _require_keys(step, ("key", "by", "locator", "attribute"), f"{filename} scenario '{scenario_id}' step 'extract_attribute_to_context'")
    if step_type == "screenshot":
        _require_keys(step, ("path",), f"{filename} scenario '{scenario_id}' step 'screenshot'")
    if step_type == "select_option":
        _require_keys(step, ("by", "locator"), f"{filename} scenario '{scenario_id}' step 'select_option'")
        if not any(key in step for key in ("value", "visible_text", "index")):
            raise ValueError(f"{filename}: select_option exige 'value', 'visible_text' ou 'index'.")
    if step_type in {"wait_until_url_contains", "wait_until_title_contains"}:
        _require_keys(step, ("value",), f"{filename} scenario '{scenario_id}' step '{step_type}'")
    if step_type == "sleep":
        _require_keys(step, ("seconds",), f"{filename} scenario '{scenario_id}' step 'sleep'")
    if step_type == "sleep_random":
        _require_keys(step, ("min_seconds", "max_seconds"), f"{filename} scenario '{scenario_id}' step 'sleep_random'")
    if step_type == "notify":
        _require_keys(step, ("message",), f"{filename} scenario '{scenario_id}' step 'notify'")
    if step_type == "http_request":
        _require_keys(step, ("url",), f"{filename} scenario '{scenario_id}' step 'http_request'")
    if step_type == "set_context":
        _require_keys(step, ("key",), f"{filename} scenario '{scenario_id}' step 'set_context'")
    if step_type == "format_context":
        _require_keys(step, ("key", "template"), f"{filename} scenario '{scenario_id}' step 'format_context'")
    if step_type == "repeat" and int(step["times"]) <= 0:
        raise ValueError(f"{filename}: 'repeat.times' doit etre > 0.")
    if step_type == "parallel":
        unsupported = [
            child["type"]
            for child in step["steps"]
            if child["type"] not in {"sleep", "sleep_random", "notify", "http_request", "require_enterprise_network", "set_context", "format_context"}
        ]
        if unsupported:
            raise ValueError(f"{filename}: bloc parallel non supporte pour: {', '.join(sorted(set(unsupported)))}")


def _validate_step_payload(step_type: str, payload: dict[str, Any], filename: str, scenario_id: str) -> None:
    if step_type == "repeat" and int(payload["times"]) <= 0:
        raise ValueError(f"{filename}: 'repeat.times' doit etre > 0.")


def _build_extract_target(payload: dict[str, Any]) -> dict[str, Any]:
    target = ExtractTarget(
        key=str(payload["key"]),
        by=str(payload["by"]),
        locator=str(payload["locator"]),
        timeout=int(payload.get("timeout", 30)),
        attribute=str(payload["attribute"]) if "attribute" in payload else None,
    )
    return {"target": target}
