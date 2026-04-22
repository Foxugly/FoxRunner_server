from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from accounts.models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    model = User
    ordering = ("email",)
    list_display = ("email", "is_active", "is_staff", "is_superuser", "is_verified", "timezone_name")
    list_filter = ("is_active", "is_staff", "is_superuser", "is_verified")
    search_fields = ("email",)
    readonly_fields = ("id", "date_joined", "last_login")

    fieldsets = (
        (None, {"fields": ("id", "email", "password")}),
        ("Profil", {"fields": ("timezone_name",)}),
        ("Statut", {"fields": ("is_active", "is_verified")}),
        ("Permissions", {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Journal", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "password1", "password2", "is_staff", "is_superuser", "is_verified")}),
    )
