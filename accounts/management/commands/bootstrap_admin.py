"""``manage.py bootstrap_admin --email <email>``.

Replaces ``scripts/bootstrap_admin.py`` from the FastAPI tree. Idempotent:
creates the user if missing, otherwise promotes the existing record to
active + verified + superuser. The password is read from the
``BOOTSTRAP_PASSWORD`` env var or interactively from stdin -- never as a
CLI flag (security policy from CHANGELOG; documented under
``docs/security.md``).
"""

from __future__ import annotations

import os
from getpass import getpass

from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = "Create or promote a FoxRunner superuser. Reads password from BOOTSTRAP_PASSWORD env or stdin."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)

    def handle(self, *args, email: str, **opts):
        password = os.environ.get("BOOTSTRAP_PASSWORD") or getpass("Mot de passe: ")
        if len(password) < 8:
            raise CommandError("Mot de passe trop court (min 8 caracteres).")
        user, created = User.objects.get_or_create(
            email=email,
            defaults={"is_superuser": True, "is_staff": True, "is_active": True, "is_verified": True},
        )
        user.is_superuser = True
        user.is_staff = True
        user.is_active = True
        user.is_verified = True
        user.set_password(password)
        user.save()
        verb = "created" if created else "promoted"
        self.stdout.write(self.style.SUCCESS(f"{verb}:{email}"))
