from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.catalog import seed_catalog_from_json
from api.db import Base
from app.config import AppConfig, NetworkConfig, RuntimeConfig, TaskConfig
from app.logger import Logger
from app.notifier import Notifier
from scenarios.loader import ScenarioData, ScenarioDefinition
from scheduler.model import TimeSlot
from scheduler.service import SchedulerService


def fake_user(email: str, superuser: bool = False):
    return SimpleNamespace(
        id=uuid.uuid4(),
        email=email,
        is_superuser=superuser,
        is_active=True,
        is_verified=True,
        timezone_name="Europe/Brussels",
    )


def setup_empty_test_db(tmp: str):
    db_path = Path(tmp) / "auth.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(setup())
    return session_maker, engine


def setup_test_db(tmp: str, service: SchedulerService):
    db_path = Path(tmp) / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            await seed_catalog_from_json(session, service.config.runtime.scenarios_file, service.config.runtime.slots_file)

    asyncio.run(setup())
    return session_maker, engine


def build_service(tmp: str) -> SchedulerService:
    base = Path(tmp)
    scenarios_file = base / "scenarios.json"
    scenarios_file.write_text(
        """
{
  "schema_version": 1,
  "data": {},
  "scenarios": {
    "alice_scenario": {
      "user_id": "alice",
      "description": "Alice",
      "steps": []
    },
    "bob_scenario": {
      "user_ids": ["bob"],
      "description": "Bob",
      "steps": []
    }
  }
}
""".strip(),
        encoding="utf-8",
    )
    slots_file = base / "slots.json"
    slots_file.write_text(
        """
{
  "slots": [
    {
      "id": "alice_slot",
      "days": [0, 1, 2, 3, 4, 5, 6],
      "start": "00:00",
      "end": "23:59",
      "scenario": "alice_scenario"
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )
    runtime = RuntimeConfig(
        timezone_name="Europe/Brussels",
        check_interval_seconds=10,
        countdown_threshold_seconds=300,
        network_retry_seconds=10,
        planning_notification_cooldown_seconds=900,
        lock_stale_seconds=100,
        state_dir=base,
        lock_file=base / "scheduler.lock",
        execution_history_file=base / "executions.json",
        next_execution_file=base / "next.json",
        last_run_file=base / "last_run.json",
        slots_file=slots_file,
        scenarios_file=scenarios_file,
        history_file=base / "history.jsonl",
        artifacts_dir=base / "artifacts",
        log_file=None,
        log_max_bytes=1024,
        log_backup_count=2,
        log_json=False,
    )
    return SchedulerService(
        config=AppConfig(
            task=TaskConfig(),
            network=NetworkConfig((), (), (), (), (), (), (), 1.0, (), True),
            runtime=runtime,
            debug_enabled=False,
        ),
        logger=Logger(debug_enabled=False),
        notifier=Notifier(None, Logger(debug_enabled=False)),
        network_guard=type(
            "Guard",
            (),
            {
                "is_default_network_available": lambda self, context="": True,
                "is_network_available_by_key": lambda self, key: True,
            },
        )(),
        slots=(TimeSlot("alice_slot", (0, 1, 2, 3, 4, 5, 6), 0, 0, 23, 59, "alice_scenario"),),
        scenarios={
            "alice_scenario": ScenarioDefinition("alice_scenario", "Alice", steps=()),
            "bob_scenario": ScenarioDefinition("bob_scenario", "Bob", steps=()),
        },
        scenario_data=ScenarioData(pushovers={}, networks={}, default_pushover_key=None, default_network_key=None),
    )


class temp_service_db:
    def __enter__(self):
        self.tmp = TemporaryDirectory()
        self.service = build_service(self.tmp.name)
        self.session_maker, self.engine = setup_test_db(self.tmp.name, self.service)
        return self.tmp.name, self.service, self.session_maker, self.engine

    def __exit__(self, exc_type, exc, tb):
        asyncio.run(self.engine.dispose())
        self.tmp.cleanup()
        return False
