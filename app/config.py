from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv() -> None:
        return None


load_dotenv()


def _env_or_default(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


@dataclass(frozen=True)
class PushoverConfig:
    token: str
    user_key: str
    sound: str = "vibrate"
    timeout_seconds: float = 20.0


@dataclass(frozen=True)
class TaskConfig:
    browser_window_size: str = "1280,900"
    page_load_timeout_seconds: int = 60
    headless: bool = False


@dataclass(frozen=True)
class NetworkConfig:
    office_ipv4_networks: tuple[str, ...]
    office_gateway_networks: tuple[str, ...]
    office_dns_suffixes: tuple[str, ...]
    vpn_interface_keywords: tuple[str, ...]
    vpn_process_names: tuple[str, ...]
    internal_test_hosts: tuple[str, ...]
    internal_test_ports: tuple[int, ...]
    tcp_timeout_seconds: float
    home_like_networks: tuple[str, ...]
    allow_private_non_home_heuristic_for_vpn: bool


@dataclass(frozen=True)
class RuntimeConfig:
    timezone_name: str
    check_interval_seconds: int
    countdown_threshold_seconds: int
    network_retry_seconds: int
    planning_notification_cooldown_seconds: int
    lock_stale_seconds: int
    state_dir: Path
    lock_file: Path
    execution_history_file: Path
    next_execution_file: Path
    last_run_file: Path
    slots_file: Path
    scenarios_file: Path
    history_file: Path
    artifacts_dir: Path
    log_file: Path | None
    log_max_bytes: int
    log_backup_count: int
    log_json: bool = False

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)


@dataclass(frozen=True)
class AppConfig:
    task: TaskConfig
    network: NetworkConfig
    runtime: RuntimeConfig
    debug_enabled: bool


def load_config() -> AppConfig:
    state_dir = Path(os.getenv("APP_STATE_DIR", ".runtime"))
    return AppConfig(
        task=TaskConfig(
            browser_window_size=_env_or_default("TASK_WINDOW_SIZE", "1280,900"),
            page_load_timeout_seconds=int(_env_or_default("TASK_PAGELOAD_TIMEOUT_SECONDS", "60")),
            headless=_env_or_default("TASK_HEADLESS", "false").lower() == "true",
        ),
        network=NetworkConfig(
            office_ipv4_networks=(),
            office_gateway_networks=(),
            office_dns_suffixes=(),
            vpn_interface_keywords=(),
            vpn_process_names=(),
            internal_test_hosts=(),
            internal_test_ports=(),
            tcp_timeout_seconds=1.0,
            home_like_networks=(),
            allow_private_non_home_heuristic_for_vpn=True,
        ),
        runtime=RuntimeConfig(
            timezone_name=_env_or_default("APP_TIMEZONE", "Europe/Brussels"),
            check_interval_seconds=int(_env_or_default("APP_CHECK_INTERVAL_SECONDS", "10")),
            countdown_threshold_seconds=int(_env_or_default("APP_COUNTDOWN_THRESHOLD_SECONDS", "300")),
            network_retry_seconds=int(_env_or_default("APP_NETWORK_RETRY_SECONDS", "10")),
            planning_notification_cooldown_seconds=int(_env_or_default("APP_PLANNING_NOTIFICATION_COOLDOWN_SECONDS", "900")),
            lock_stale_seconds=int(_env_or_default("APP_LOCK_STALE_SECONDS", "43200")),
            state_dir=state_dir,
            lock_file=state_dir / "scheduler.lock",
            execution_history_file=state_dir / "executions.json",
            next_execution_file=state_dir / "next.json",
            last_run_file=state_dir / "last_run.json",
            history_file=state_dir / "history.jsonl",
            artifacts_dir=Path(_env_or_default("APP_ARTIFACTS_DIR", str(state_dir / "artifacts"))),
            log_file=_build_optional_log_file(state_dir),
            log_max_bytes=int(_env_or_default("APP_LOG_MAX_BYTES", "1048576")),
            log_backup_count=int(_env_or_default("APP_LOG_BACKUP_COUNT", "3")),
            log_json=_env_or_default("APP_LOG_JSON", "false").lower() == "true",
            slots_file=Path(_env_or_default("APP_SLOTS_FILE", "config/slots.json")),
            scenarios_file=Path(_env_or_default("APP_SCENARIOS_FILE", "config/scenarios.json")),
        ),
        debug_enabled=_env_or_default("APP_DEBUG", "true").lower() == "true",
    )


def _build_optional_log_file(state_dir: Path) -> Path | None:
    raw = os.getenv("APP_LOG_FILE")
    if raw is None or raw == "":
        return None
    return Path(raw) if raw.lower() != "default" else state_dir / "app.log"
