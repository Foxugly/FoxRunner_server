from __future__ import annotations

from django.contrib import admin

from catalog.models import Scenario, ScenarioShare, Slot


@admin.register(Scenario)
class ScenarioAdmin(admin.ModelAdmin):
    list_display = ("scenario_id", "owner", "description", "updated_at")
    list_filter = ("created_at", "updated_at")
    search_fields = ("scenario_id", "owner__email", "description")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-updated_at",)
    raw_id_fields = ("owner",)


@admin.register(ScenarioShare)
class ScenarioShareAdmin(admin.ModelAdmin):
    list_display = ("scenario", "user")
    list_filter = ("scenario",)
    search_fields = ("scenario__scenario_id", "user__email")
    readonly_fields = ("id",)
    raw_id_fields = ("user",)


@admin.register(Slot)
class SlotAdmin(admin.ModelAdmin):
    list_display = ("slot_id", "scenario", "days", "start", "end", "enabled")
    list_filter = ("enabled", "scenario")
    search_fields = ("slot_id", "scenario__scenario_id")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("scenario", "slot_id")
