"""Custom User model.

Mirrors the FastAPI User shape (UUID primary key, email as the login
identifier, timezone profile, superuser/verified flags). ``AbstractBaseUser +
PermissionsMixin`` gives full control over the authentication surface
without inheriting ``AbstractUser``'s ``username`` field.
"""

from __future__ import annotations

import uuid
from zoneinfo import ZoneInfo

from django.conf import settings
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


class PushItTarget(models.Model):
    """A per-user PushIT application the scheduler/scenarios can notify.

    Each row is owned by a single user and is never shared: a user only
    ever sees and edits their own targets (the CRUD endpoints scope every
    query to ``request.auth``). The ``app_token`` is the PushIT app token
    (header ``X-App-Token``); it is a secret but is returned to its own
    owner so the frontend editor can display/modify it.

    The CLI scheduler resolves the config of a scenario's *owner* from
    this table (it reads the same local DB via Django), so notifications
    follow whoever owns the scenario rather than a single shared token.
    Exactly one target per owner may carry ``is_default=True`` (enforced
    in :meth:`save`).
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pushit_targets",
        db_column="owner_user_id",
    )
    name = models.CharField(max_length=64)
    app_token = models.CharField(max_length=255)
    base_url = models.CharField(max_length=255, default="https://pushit-api.foxugly.com/api/v1")
    title = models.CharField(max_length=128, default="FoxRunner")
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pushit_targets"
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="uq_pushit_target_owner_name"),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.owner_id})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # A single default per owner: when this row becomes the default,
        # clear the flag on the owner's other targets in the same write.
        if self.is_default:
            PushItTarget.objects.filter(owner_id=self.owner_id, is_default=True).exclude(pk=self.pk).update(is_default=False)
