from accounts.models import User
from django.test import TestCase
from ninja.errors import HttpError

from catalog.models import Scenario
from catalog.permissions import _is_scenario_owner, require_scenario_owner, scenario_role


class CatalogPermissionsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(email="alice@x.com", password="x")
        cls.bob = User.objects.create_user(email="bob@x.com", password="x")
        cls.admin = User.objects.create_superuser(email="admin@x.com", password="x")
        # Owned by Alice via UUID
        cls.s_uuid = Scenario.objects.create(scenario_id="s_uuid", owner_user_id=str(cls.alice.id))
        # Owned by Alice via email (legacy JSON-seed shape)
        cls.s_email = Scenario.objects.create(scenario_id="s_email", owner_user_id="alice@x.com")
        # Owned by neither
        cls.s_other = Scenario.objects.create(scenario_id="s_other", owner_user_id="default")

    def test_owner_match_by_uuid(self):
        self.assertTrue(_is_scenario_owner(self.s_uuid, self.alice))

    def test_owner_match_by_email_dual_stack(self):
        self.assertTrue(_is_scenario_owner(self.s_email, self.alice))

    def test_non_owner_rejected(self):
        with self.assertRaises(HttpError) as ctx:
            require_scenario_owner(self.s_uuid, self.bob)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_admin_always_writable(self):
        require_scenario_owner(self.s_other, self.admin)
        role, writable = scenario_role(self.s_other, self.admin)
        self.assertEqual(role, "superuser")
        self.assertTrue(writable)

    def test_role_owner(self):
        role, writable = scenario_role(self.s_email, self.alice)
        self.assertEqual(role, "owner")
        self.assertTrue(writable)

    def test_role_reader(self):
        role, writable = scenario_role(self.s_other, self.bob)
        self.assertEqual(role, "reader")
        self.assertFalse(writable)
