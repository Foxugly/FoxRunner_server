from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class SlotRunRecord:
    slot_key: str
    executed_at: str


class ExecutionStateStore:
    def __init__(self, history_file: Path):
        self.history_file = history_file
        self.history_file.parent.mkdir(parents=True, exist_ok=True)

    def has_executed(self, slot_key: str) -> bool:
        data = self._load()
        return slot_key in data.get("executed_slots", {})

    def mark_executed(self, slot_key: str, executed_at: datetime) -> None:
        data = self._load()
        executed_slots = data.setdefault("executed_slots", {})
        executed_slots[slot_key] = SlotRunRecord(
            slot_key=slot_key,
            executed_at=_isoformat(executed_at),
        ).__dict__
        self._prune_old_entries(executed_slots)
        self._save(data)

    def _load(self) -> dict:
        if not self.history_file.exists():
            return {"executed_slots": {}}

        try:
            with self.history_file.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (json.JSONDecodeError, OSError):
            return {"executed_slots": {}}

    def _save(self, data: dict) -> None:
        temp_file = self.history_file.with_suffix(".tmp")
        with temp_file.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        temp_file.replace(self.history_file)

    def _prune_old_entries(self, executed_slots: dict) -> None:
        threshold = datetime.now(UTC).date().isoformat()
        stale_keys = [key for key in executed_slots if key.split("|", 1)[0] < threshold]
        for key in stale_keys:
            executed_slots.pop(key, None)


class NextExecutionStore:
    def __init__(self, next_execution_file: Path):
        self.next_execution_file = next_execution_file
        self.next_execution_file.parent.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        slot_key: str,
        scheduled_for: datetime,
        status: str,
        details: str | None = None,
        *,
        slot_id: str | None = None,
        scenario_id: str | None = None,
        execution_id: str | None = None,
    ) -> None:
        payload = {
            "slot_key": slot_key,
            "scheduled_for": _isoformat(scheduled_for),
            "status": status,
            "updated_at": _utc_now_iso(),
        }
        if slot_id:
            payload["slot_id"] = slot_id
        if scenario_id:
            payload["scenario_id"] = scenario_id
        if execution_id:
            payload["execution_id"] = execution_id
        if details:
            payload["details"] = details
        temp_file = self.next_execution_file.with_suffix(".tmp")
        with temp_file.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        temp_file.replace(self.next_execution_file)

    def clear(self) -> None:
        with contextlib.suppress(OSError):
            self.next_execution_file.unlink(missing_ok=True)


class LastRunStore:
    def __init__(self, last_run_file: Path):
        self.last_run_file = last_run_file
        self.last_run_file.parent.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        *,
        slot_key: str,
        slot_id: str | None = None,
        scenario_id: str | None = None,
        execution_id: str | None = None,
        executed_at: datetime,
        status: str,
        step: str,
        message: str,
    ) -> None:
        payload = {
            "slot_key": slot_key,
            "executed_at": _isoformat(executed_at),
            "status": status,
            "step": step,
            "message": message,
            "updated_at": _utc_now_iso(),
        }
        if slot_id:
            payload["slot_id"] = slot_id
        if scenario_id:
            payload["scenario_id"] = scenario_id
        if execution_id:
            payload["execution_id"] = execution_id
        temp_file = self.last_run_file.with_suffix(".tmp")
        with temp_file.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        temp_file.replace(self.last_run_file)


class HistoryStore:
    def __init__(self, history_jsonl_file: Path):
        self.history_jsonl_file = history_jsonl_file
        self.history_jsonl_file.parent.mkdir(parents=True, exist_ok=True)
        # Two-level locking: threading.Lock for intra-process contention
        # (threaded Celery workers, CLI loop), and ProcessLock for
        # cross-process serialization (CLI scheduler + Celery beat writing
        # to the same .jsonl). ProcessLock alone is not thread-safe because
        # its single _fd field cannot describe N concurrent holders.
        self._thread_lock = threading.Lock()
        self._process_lock = ProcessLock(history_jsonl_file.with_name(history_jsonl_file.name + ".lock"), stale_seconds=60)

    def append(
        self,
        *,
        slot_key: str,
        slot_id: str,
        scenario_id: str,
        execution_id: str | None,
        executed_at: datetime,
        status: str,
        step: str,
        message: str,
    ) -> None:
        payload = {
            "slot_key": slot_key,
            "slot_id": slot_id,
            "scenario_id": scenario_id,
            "execution_id": execution_id,
            "executed_at": _isoformat(executed_at),
            "status": status,
            "step": step,
            "message": message,
            "updated_at": _utc_now_iso(),
        }
        with self._write_lock(), self.history_jsonl_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @contextmanager
    def _write_lock(self):
        # Intra-process first (threading.Lock), then cross-process
        # (ProcessLock). Short spin loop on the process lock is acceptable —
        # writes are cheap and conflicts are rare. Falls back to thread-only
        # serialization after a few retries so a stale or contended process
        # lock can never wedge scheduling.
        with self._thread_lock:
            attempts = 0
            acquired = False
            while attempts < 10:
                if self._process_lock.acquire():
                    acquired = True
                    break
                attempts += 1
                time.sleep(0.05)
            try:
                yield
            finally:
                if acquired:
                    self._process_lock.release()

    def read(
        self,
        *,
        limit: int | None = None,
        status: str | None = None,
        slot_id: str | None = None,
        scenario_id: str | None = None,
        execution_id: str | None = None,
    ) -> list[dict]:
        if not self.history_jsonl_file.exists():
            return []
        with self.history_jsonl_file.open("r", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        if status is not None:
            rows = [row for row in rows if row.get("status") == status]
        if slot_id is not None:
            rows = [row for row in rows if row.get("slot_id") == slot_id]
        if scenario_id is not None:
            rows = [row for row in rows if row.get("scenario_id") == scenario_id]
        if execution_id is not None:
            rows = [row for row in rows if row.get("execution_id") == execution_id]
        rows.reverse()
        if limit is not None:
            rows = rows[:limit]
        return rows

    def prune(self, *, older_than_days: int) -> int:
        if older_than_days < 0:
            raise ValueError("'older_than_days' doit etre >= 0.")
        if not self.history_jsonl_file.exists():
            return 0
        cutoff = time.time() - (older_than_days * 86400)
        with self._write_lock():
            with self.history_jsonl_file.open("r", encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
            kept_rows = [row for row in rows if _parse_datetime(row["executed_at"]).timestamp() >= cutoff]
            removed = len(rows) - len(kept_rows)
            temp_file = self.history_jsonl_file.with_suffix(".tmp")
            with temp_file.open("w", encoding="utf-8") as handle:
                for row in kept_rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            temp_file.replace(self.history_jsonl_file)
        return removed


class ProcessLock:
    def __init__(self, lock_file: Path, stale_seconds: int = 3600):
        # Stale detection is primarily driven by pid liveness; the timeout is a
        # backstop for stray lockfiles on platforms where the PID cannot be
        # verified. Kept short (1h default) to avoid the "scheduler silently
        # blocked for 12h after a crash" class of incidents.
        self.lock_file = lock_file
        self.stale_seconds = stale_seconds
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self._fd: int | None = None

    def acquire(self) -> bool:
        try:
            self._fd = os.open(str(self.lock_file), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            payload = json.dumps(
                {
                    "pid": os.getpid(),
                    "created_at": time.time(),
                }
            )
            os.write(self._fd, payload.encode("utf-8", errors="ignore"))
            return True
        except FileExistsError:
            if self._recover_stale_lock():
                return self.acquire()
            return False

    def release(self) -> None:
        if self._fd is None:
            return
        os.close(self._fd)
        self._fd = None
        with contextlib.suppress(OSError):
            self.lock_file.unlink(missing_ok=True)

    def _recover_stale_lock(self) -> bool:
        metadata = self._read_metadata()
        if metadata is None:
            return self._remove_lock_file()

        pid = metadata.get("pid")
        created_at = metadata.get("created_at", 0)
        is_stale = (time.time() - float(created_at)) >= self.stale_seconds
        pid_dead = not self._pid_exists(int(pid)) if pid is not None else True

        if is_stale or pid_dead:
            return self._remove_lock_file()

        return False

    def _read_metadata(self) -> dict | None:
        try:
            with self.lock_file.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def _remove_lock_file(self) -> bool:
        try:
            self.lock_file.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            try:
                import ctypes

                process_query_limited_information = 0x1000
                handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
                if not handle:
                    return False
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            except OSError:
                return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    @contextmanager
    def held(self):
        acquired = self.acquire()
        try:
            yield acquired
        finally:
            if acquired:
                self.release()


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
