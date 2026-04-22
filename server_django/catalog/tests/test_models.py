from accounts.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase

from catalog.models import Scenario, ScenarioShare, Slot


class CatalogModelSmokeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(email="alice@x.com", password="x")
        cls.bob = User.objects.create_user(email="bob@x.com", password="x")
        cls.carol = User.objects.create_user(email="carol@x.com", password="x")

    def test_scenario_round_trip(self):
        s = Scenario.objects.create(scenario_id="demo", owner=self.alice, description="d", definition={"x": 1})
        self.assertEqual(s.definition, {"x": 1})
        self.assertEqual(s.description, "d")
        self.assertEqual(s.owner_id, self.alice.id)
        self.assertIsNotNone(s.created_at)

    def test_slot_round_trip(self):
        s = Scenario.objects.create(scenario_id="demo2", owner=self.alice)
        slot = Slot.objects.create(slot_id="slot1", scenario=s, days=[0, 1], start="08:00", end="09:00")
        self.assertEqual(slot.days, [0, 1])
        self.assertTrue(slot.enabled)

    def test_share_uniqueness(self):
        s = Scenario.objects.create(scenario_id="demo3", owner=self.alice)
        ScenarioShare.objects.create(scenario=s, user=self.bob)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScenarioShare.objects.create(scenario=s, user=self.bob)
