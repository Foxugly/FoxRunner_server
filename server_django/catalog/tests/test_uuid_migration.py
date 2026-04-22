"""Tests for the catalog UUID-normalization data migration (0002).

Strategy: the live test DB has the FK in place (head migration), so we
can't seed an email-shaped owner_user_id row directly via the ORM (the
FK constraint would reject it). Instead, the tests:

1. Disable SQLite FK checks for the test connection (``PRAGMA
   foreign_keys = OFF``) so raw SQL can write a row carrying an email
   in the ``owner_user_id`` column.
2. Import the migration module via ``importlib`` (its filename starts
   with a digit) and invoke its ``normalize_owner_user_id`` callable
   directly against the live app registry.
3. Re-read via raw SQL and assert the row was rewritten.

The test ``test_normalize_preserves_unmatched`` uses a UUID string that
doesn't match any real ``User``: the migration must leave such rows
untouched.
"""

from __future__ import annotations

import importlib.util
import pathlib
import uuid

from accounts.models import User
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


def _historical_apps():
    """Apps registry at the catalog/0002 data-migration apply point."""
    executor = MigrationExecutor(connection)
    state = executor.loader.project_state(
        [
            ("accounts", "0001_initial"),
            ("catalog", "0001_initial"),
        ]
    )
    return state.apps


def _load_migration_module(app_label: str, migration_name: str):
    """Import a migration file whose name starts with a digit."""
    path = pathlib.Path(__file__).resolve().parents[2] / app_label / "migrations" / f"{migration_name}.py"
    spec = importlib.util.spec_from_file_location(f"_loaded_{app_label}_{migration_name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NormalizeOwnerUserIdMigrationTest(TransactionTestCase):
    """``TransactionTestCase`` so PRAGMA + raw SQL hit the live test DB."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration_module = _load_migration_module("catalog", "0002_normalize_owner_user_id")

    def setUp(self):
        super().setUp()
        # Defer FK enforcement so we can seed rows that violate the FK.
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys = OFF")
        self.alice = User.objects.create_user(email="alice@x.com", password="x")
        # Re-enable for the duration of the test except where we explicitly
        # need to seed via raw SQL (the helpers turn it OFF/ON locally).

    def tearDown(self):
        # Clean up any rows we created via raw SQL so the next test starts
        # from a clean slate (TransactionTestCase truncates the rest).
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM scenario_shares")
            cursor.execute("DELETE FROM scenarios")
            cursor.execute("PRAGMA foreign_keys = ON")
        super().tearDown()

    def _seed_raw_owner(self, *, scenario_id: str, owner_value: str) -> int:
        """Bypass the FK constraint to seed a legacy email-string owner."""
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys = OFF")
            cursor.execute(
                "INSERT INTO scenarios (scenario_id, owner_user_id, description, definition, created_at, updated_at) "
                "VALUES (%s, %s, '', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                [scenario_id, owner_value],
            )
            cursor.execute("SELECT id FROM scenarios WHERE scenario_id=%s", [scenario_id])
            return cursor.fetchone()[0]

    def _seed_raw_share(self, *, scenario_id_str: str, user_value: str) -> None:
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys = OFF")
            cursor.execute(
                "INSERT INTO scenario_shares (scenario_id, user_id) VALUES (%s, %s)",
                [scenario_id_str, user_value],
            )

    def _read_owner(self, scenario_id: str) -> str:
        with connection.cursor() as cursor:
            cursor.execute("SELECT owner_user_id FROM scenarios WHERE scenario_id=%s", [scenario_id])
            row = cursor.fetchone()
        return row[0] if row else ""

    def _read_share_user(self, scenario_id: str) -> str:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT user_id FROM scenario_shares WHERE scenario_id=%s",
                [scenario_id],
            )
            row = cursor.fetchone()
        return row[0] if row else ""

    def test_normalize_owner_user_id_email_to_uuid(self):
        self._seed_raw_owner(scenario_id="email-owned", owner_value=self.alice.email)
        self._seed_raw_owner(scenario_id="share-target", owner_value=str(self.alice.id))
        self._seed_raw_share(scenario_id_str="share-target", user_value=self.alice.email)

        self.migration_module.normalize_owner_user_id(_historical_apps(), schema_editor=None)

        self.assertEqual(self._read_owner("email-owned"), str(self.alice.id))
        self.assertEqual(self._read_share_user("share-target"), str(self.alice.id))

    def test_normalize_idempotent_replay(self):
        self._seed_raw_owner(scenario_id="uuid-owned", owner_value=str(self.alice.id))
        self.migration_module.normalize_owner_user_id(_historical_apps(), schema_editor=None)
        self.assertEqual(self._read_owner("uuid-owned"), str(self.alice.id))
        # Replay -- value unchanged.
        self.migration_module.normalize_owner_user_id(_historical_apps(), schema_editor=None)
        self.assertEqual(self._read_owner("uuid-owned"), str(self.alice.id))

    def test_normalize_preserves_unmatched(self):
        # A UUID string that does NOT correspond to any User. Pre-phase-5
        # the column also held opaque strings like "default"; post-phase-5
        # the FK promotion (0003) would have rejected those, so the
        # only realistic unmatched shape on a normalized DB is an
        # orphaned UUID. The migration must leave it intact.
        orphan_uuid = str(uuid.uuid4())
        self._seed_raw_owner(scenario_id="orphan-uuid", owner_value=orphan_uuid)
        self.migration_module.normalize_owner_user_id(_historical_apps(), schema_editor=None)
        self.assertEqual(self._read_owner("orphan-uuid"), orphan_uuid)
