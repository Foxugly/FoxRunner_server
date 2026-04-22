from django.db import IntegrityError, transaction
from django.test import TestCase

from catalog.models import Scenario, ScenarioShare, Slot


class CatalogModelSmokeTest(TestCase):
    def test_scenario_round_trip(self):
        s = Scenario.objects.create(scenario_id="demo", owner_user_id="00000000-0000-0000-0000-000000000001", description="d", definition={"x": 1})
        self.assertEqual(s.definition, {"x": 1})
        self.assertEqual(s.description, "d")
        self.assertIsNotNone(s.created_at)

    def test_slot_round_trip(self):
        s = Scenario.objects.create(scenario_id="demo2", owner_user_id="00000000-0000-0000-0000-000000000002")
        slot = Slot.objects.create(slot_id="slot1", scenario=s, days=[0, 1], start="08:00", end="09:00")
        self.assertEqual(slot.days, [0, 1])
        self.assertTrue(slot.enabled)

    def test_share_uniqueness(self):
        s = Scenario.objects.create(scenario_id="demo3", owner_user_id="00000000-0000-0000-0000-000000000003")
        ScenarioShare.objects.create(scenario=s, user_id="00000000-0000-0000-0000-000000000004")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScenarioShare.objects.create(scenario=s, user_id="00000000-0000-0000-0000-000000000004")
