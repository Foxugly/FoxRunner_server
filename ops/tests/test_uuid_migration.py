"""Tests for the ops UUID-normalization data migration (0002).

Same raw-SQL strategy as ``catalog/tests/test_uuid_migration.py``: the
live test DB has the FK promotion in place, so we toggle SQLite FK
checks to seed legacy email-shaped values directly into ``audit_log``,
invoke the migration callable against a historical apps registry, then
read back via raw SQL.

``Job.user_id`` and ``IdempotencyKey.user_id`` are intentionally not
covered: they only ever held UUID strings (always API-created), so the
data migration leaves them alone.
"""

from __future__ import annotations

import importlib.util
import pathlib
import uuid

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from accounts.models import User


def _load_migration_module(app_label: str, migration_name: str):
    """Import a migration file whose name starts with a digit."""
    path = pathlib.Path(__file__).resolve().parents[2] / app_label / "migrations" / f"{migration_name}.py"
    spec = importlib.util.spec_from_file_location(f"_loaded_{app_label}_{migration_name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _historical_apps():
    """Apps registry at the ops/0002 data-migration apply point."""
    executor = MigrationExecutor(connection)
    state = executor.loader.project_state(
        [
            ("accounts", "0001_initial"),
            ("ops", "0001_initial"),
        ]
    )
    return state.apps


class NormalizeActorUserIdMigrationTest(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration_module = _load_migration_module("ops", "0002_normalize_actor_user_id")

    def setUp(self):
        super().setUp()
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys = OFF")
        self.alice = User.objects.create_user(email="alice@x.com", password="x")

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM audit_log")
            cursor.execute("PRAGMA foreign_keys = ON")
        super().tearDown()

    def _seed_raw_audit(self, *, target_id: str, actor_value: str) -> None:
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys = OFF")
            cursor.execute(
                "INSERT INTO audit_log (actor_user_id, action, target_type, target_id, before, after, created_at) "
                "VALUES (%s, 'system.test', 'scenario', %s, '{}', '{}', CURRENT_TIMESTAMP)",
                [actor_value, target_id],
            )

    def _read_actor(self, target_id: str) -> str:
        with connection.cursor() as cursor:
            cursor.execute("SELECT actor_user_id FROM audit_log WHERE target_id=%s", [target_id])
            row = cursor.fetchone()
        return row[0] if row else ""

    def test_normalize_actor_user_id_email_to_uuid(self):
        self._seed_raw_audit(target_id="email-actor", actor_value=self.alice.email)
        self.migration_module.normalize_actor_user_id(_historical_apps(), schema_editor=None)
        self.assertEqual(self._read_actor("email-actor"), str(self.alice.id))

    def test_normalize_idempotent_replay(self):
        self._seed_raw_audit(target_id="uuid-actor", actor_value=str(self.alice.id))
        self.migration_module.normalize_actor_user_id(_historical_apps(), schema_editor=None)
        self.assertEqual(self._read_actor("uuid-actor"), str(self.alice.id))
        # Replay -- value unchanged.
        self.migration_module.normalize_actor_user_id(_historical_apps(), schema_editor=None)
        self.assertEqual(self._read_actor("uuid-actor"), str(self.alice.id))

    def test_normalize_preserves_unmatched(self):
        # An orphan UUID (no matching User) must be left intact.
        orphan_uuid = str(uuid.uuid4())
        self._seed_raw_audit(target_id="orphan-actor", actor_value=orphan_uuid)
        self.migration_module.normalize_actor_user_id(_historical_apps(), schema_editor=None)
        self.assertEqual(self._read_actor("orphan-actor"), orphan_uuid)
