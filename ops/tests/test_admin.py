from django.contrib import admin
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from ops.models import (
    AppSetting,
    AuditEntry,
    ExecutionHistory,
    GraphNotification,
    GraphSubscription,
    IdempotencyKey,
    Job,
    JobEvent,
)


class OpsAdminSmokeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(email="admin@x.com", password="passw0rd!")

    def test_models_registered(self):
        for model in (Job, JobEvent, GraphSubscription, GraphNotification, AuditEntry, ExecutionHistory, AppSetting, IdempotencyKey):
            self.assertIn(model, admin.site._registry, f"{model.__name__} not registered in admin")

    def test_changelist_responds(self):
        self.client.force_login(self.superuser)
        for model in (Job, JobEvent, GraphSubscription, GraphNotification, AuditEntry, ExecutionHistory, AppSetting, IdempotencyKey):
            url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, f"{model.__name__} changelist failed: {response.status_code}")
