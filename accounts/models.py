"""Custom User model.

Mirrors the FastAPI User shape (UUID primary key, email as the login
identifier, timezone profile, superuser/verified flags). ``AbstractBaseUser +
PermissionsMixin`` gives full control over the authentication surface
without inheriting ``AbstractUser``'s ``username`` field.
"""

from __future__ import annotations

import uuid
from zoneinfo import ZoneInfo

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models


def _validate_timezone_name(value: str) -> None:
    try:
        ZoneInfo(value)
    except Exception as exc:
        raise ValidationError(f"Timezone IANA invalide: {value}") from exc


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("L'adresse email est requise.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_active", True)
        extra.setdefault("is_verified", True)
        return self._create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, max_length=254)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)
    timezone_name = models.CharField(max_length=64, default="Europe/Brussels", validators=[_validate_timezone_name])
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "accounts_user"

    def __str__(self) -> str:
        return self.email
