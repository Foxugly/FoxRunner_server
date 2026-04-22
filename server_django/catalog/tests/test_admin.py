from accounts.models import User
from django.contrib import admin
from django.test import TestCase
from django.urls import reverse

from catalog.models import Scenario, ScenarioShare, Slot


class CatalogAdminSmokeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(email="admin@x.com", password="passw0rd!")

    def test_models_registered(self):
        for model in (Scenario, ScenarioShare, Slot):
            self.assertIn(model, admin.site._registry, f"{model.__name__} not registered in admin")

    def test_changelist_responds(self):
        self.client.force_login(self.superuser)
        for opts in (Scenario._meta, ScenarioShare._meta, Slot._meta):
            url = reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, f"{opts.label} changelist failed: {response.status_code}")
