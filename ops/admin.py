from __future__ import annotations

from django.contrib import admin

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


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("job_id", "kind", "status", "user", "target_id", "dry_run", "started_at", "finished_at")
    list_filter = ("status", "kind", "dry_run")
    search_fields = ("job_id", "celery_task_id", "user__email", "target_id")
    readonly_fields = ("id", "created_at", "updated_at", "started_at", "finished_at")
    ordering = ("-created_at",)
    raw_id_fields = ("user",)


@admin.register(JobEvent)
class JobEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "level", "job", "step", "created_at")
    list_filter = ("event_type", "level")
    search_fields = ("job__job_id", "step", "message")
    readonly_fields = ("id", "created_at")
    ordering = ("-created_at",)


@admin.register(GraphSubscription)
class GraphSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("subscription_id", "resource", "change_type", "expiration_datetime", "updated_at")
    list_filter = ("change_type",)
    search_fields = ("subscription_id", "resource")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-updated_at",)


@admin.register(GraphNotification)
class GraphNotificationAdmin(admin.ModelAdmin):
    list_display = ("subscription_id", "change_type", "resource", "lifecycle_event", "created_at")
    list_filter = ("change_type", "lifecycle_event")
    search_fields = ("subscription_id", "resource", "tenant_id")
    readonly_fields = ("id", "created_at")
    ordering = ("-created_at",)


@admin.register(AuditEntry)
class AuditEntryAdmin(admin.ModelAdmin):
    list_display = ("action", "target_type", "target_id", "actor", "created_at")
    list_filter = ("action", "target_type")
    search_fields = ("actor__email", "target_id")
    readonly_fields = ("id", "created_at")
    ordering = ("-created_at",)
    raw_id_fields = ("actor",)


@admin.register(ExecutionHistory)
class ExecutionHistoryAdmin(admin.ModelAdmin):
    list_display = ("scenario_id", "slot_id", "status", "executed_at", "step")
    list_filter = ("status",)
    search_fields = ("scenario_id", "slot_id", "execution_id", "slot_key")
    readonly_fields = ("id", "updated_at")
    ordering = ("-executed_at",)


@admin.register(AppSetting)
class AppSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "description", "updated_by", "updated_at")
    search_fields = ("key", "description")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("key",)


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ("user_id", "key", "status_code", "created_at")
    list_filter = ("status_code",)
    search_fields = ("user_id", "key")
    readonly_fields = ("id", "created_at")
    ordering = ("-created_at",)
