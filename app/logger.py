from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    from colorama import Fore, Style, init

    init(autoreset=True)
    COLORAMA_AVAILABLE = True
except ImportError:
    COLORAMA_AVAILABLE = False


class Logger:
    def __init__(
        self,
        debug_enabled: bool = True,
        log_file: Path | None = None,
        max_bytes: int = 1048576,
        backup_count: int = 3,
        json_enabled: bool = False,
    ):
        self.debug_enabled = debug_enabled
        self.log_file = log_file
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.json_enabled = json_enabled
        if self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)

        if COLORAMA_AVAILABLE:
            self.colors = {
                "INFO": Fore.CYAN,
                "SUCCESS": Fore.GREEN,
                "WARNING": Fore.YELLOW,
                "ERROR": Fore.RED,
                "DEBUG": Fore.MAGENTA,
                "RESET": Style.RESET_ALL,
            }
        else:
            self.colors = {
                "INFO": "\033[96m",
                "SUCCESS": "\033[92m",
                "WARNING": "\033[93m",
                "ERROR": "\033[91m",
                "DEBUG": "\033[95m",
                "RESET": "\033[0m",
            }

    def _log(self, level: str, message: str):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        line = f"[{now}] [{level}] {message}"
        payload = {"timestamp": now, "level": level, "message": message}
        color = self.colors.get(level, "")
        reset = self.colors["RESET"]
        if os.getenv("APP_ENV", "").lower() != "test" and os.getenv("APP_LOG_CONSOLE_ENABLED", "true").lower() == "true":
            print(json.dumps(payload, ensure_ascii=False) if self.json_enabled else f"{color}{line}{reset}")
        if self.log_file is not None:
            self._rotate_if_needed()
            with self.log_file.open("a", encoding="utf-8") as handle:
                handle.write((json.dumps(payload, ensure_ascii=False) if self.json_enabled else line) + "\n")

    def _rotate_if_needed(self) -> None:
        if self.log_file is None or not self.log_file.exists():
            return
        if self.log_file.stat().st_size < self.max_bytes:
            return
        for index in range(self.backup_count - 1, 0, -1):
            src = self.log_file.with_suffix(self.log_file.suffix + f".{index}")
            dst = self.log_file.with_suffix(self.log_file.suffix + f".{index + 1}")
            if src.exists():
                dst.unlink(missing_ok=True)
                src.replace(dst)
        first_backup = self.log_file.with_suffix(self.log_file.suffix + ".1")
        first_backup.unlink(missing_ok=True)
        self.log_file.replace(first_backup)

    def info(self, message: str):
        self._log("INFO", message)

    def success(self, message: str):
        self._log("SUCCESS", message)

    def warning(self, message: str):
        self._log("WARNING", message)

    def error(self, message: str):
        self._log("ERROR", message)

    def debug(self, message: str):
        if self.debug_enabled:
            self._log("DEBUG", message)
