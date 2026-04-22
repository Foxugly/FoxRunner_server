"""Tests for the ``bootstrap_admin`` management command."""

from __future__ import annotations

import os

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounts.models import User


class BootstrapAdminTest(TestCase):
    def test_creates_superuser_from_env(self):
        os.environ["BOOTSTRAP_PASSWORD"] = "S3cret!Strong"
        try:
            call_command("bootstrap_admin", "--email", "boot@x.com")
        finally:
            del os.environ["BOOTSTRAP_PASSWORD"]
        u = User.objects.get(email="boot@x.com")
        self.assertTrue(u.is_superuser)
        self.assertTrue(u.is_staff)
        self.assertTrue(u.is_active)
        self.assertTrue(u.is_verified)
        self.assertTrue(u.check_password("S3cret!Strong"))

    def test_idempotent_promotes_existing(self):
        User.objects.create_user(email="exist@x.com", password="initial-pass")
        os.environ["BOOTSTRAP_PASSWORD"] = "Newp4ss!"
        try:
            call_command("bootstrap_admin", "--email", "exist@x.com")
        finally:
            del os.environ["BOOTSTRAP_PASSWORD"]
        u = User.objects.get(email="exist@x.com")
        self.assertTrue(u.is_superuser)
        self.assertTrue(u.is_staff)
        self.assertTrue(u.is_active)
        self.assertTrue(u.is_verified)
        self.assertTrue(u.check_password("Newp4ss!"))

    def test_short_password_rejected(self):
        os.environ["BOOTSTRAP_PASSWORD"] = "short"
        try:
            with self.assertRaises(CommandError):
                call_command("bootstrap_admin", "--email", "boot@x.com")
        finally:
            del os.environ["BOOTSTRAP_PASSWORD"]
        # Nothing should have been persisted.
        self.assertFalse(User.objects.filter(email="boot@x.com").exists())
