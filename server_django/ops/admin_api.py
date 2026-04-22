"""Ninja router for admin / audit / settings / artifacts / monitoring.

All endpoints land under ``/api/v1/`` and require a superuser
(``accounts.permissions.require_superuser``). The router is mounted from
``foxrunner.api`` alongside the catalog / accounts / ops routers.

Different endpoint groups carry different ``tags=[...]`` so the OpenAPI
schema mirrors the FastAPI grouping (``admin``, ``audit``, ``artifacts``,
``monitoring``).
"""

from __future__ import annotations

from accounts.permissions import require_superuser
from django.http import FileResponse, HttpResponse
from foxrunner.pagination import page_response
from ninja import Body, Query, Router

from ops import services as ops_services
from ops.schemas import (
    AdminUserPatchIn,
    AppSettingIn,
    AppSettingOut,
    AppSettingPage,
    ArtifactPage,
    AuditPage,
    ConfigChecksOut,
    DbStatsOut,
    ExportOut,
    ImportDryRun,
    MonitoringSummary,
    RetentionResult,
)

router = Router(tags=["admin"])


# --------------------------------------------------------------------------
# /admin/users (list + PATCH)
# --------------------------------------------------------------------------


@router.get("/admin/users", tags=["admin"])
def admin_list_users_endpoint(
    request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Paginated list of all users. Superuser only."""
    require_superuser(request.auth)
    from accounts.models import User

    qs = User.objects.all().order_by("email")
    total = qs.count()
    items = [ops_services.serialize_user(user) for user in qs[offset : offset + limit]]
    return page_response(items, total=total, limit=limit, offset=offset)


@router.patch("/admin/users/{target_user_id}", tags=["admin"])
def admin_update_user_endpoint(request, target_user_id: str, payload: AdminUserPatchIn):
    """PATCH /admin/users/{target_user_id}.

    ``target_user_id`` accepts either a UUID or an email (consistent with
    the user-scoped routes elsewhere in the API).
    """
    require_superuser(request.auth)
    return ops_services.update_admin_user(
        target_user_id=target_user_id,
        is_active=payload.is_active,
        is_superuser=payload.is_superuser,
        is_verified=payload.is_verified,
        timezone_name=payload.timezone_name,
        current_user=request.auth,
    )


# --------------------------------------------------------------------------
# /admin/config-checks + /admin/db-stats
# --------------------------------------------------------------------------


@router.get("/admin/config-checks", response=ConfigChecksOut, tags=["admin"])
def admin_config_checks_endpoint(request):
    require_superuser(request.auth)
    return ops_services.config_checks()


@router.get("/admin/db-stats", response=DbStatsOut, tags=["admin"])
def admin_db_stats_endpoint(request):
    require_superuser(request.auth)
    return ops_services.db_stats()


# --------------------------------------------------------------------------
# /admin/export + /admin/import
# --------------------------------------------------------------------------


@router.get("/admin/export", response=ExportOut, tags=["admin"])
def admin_export_endpoint(request):
    require_superuser(request.auth)
    return ops_services.export_catalog()


@router.post("/admin/import", response=ImportDryRun, tags=["admin"])
def admin_import_endpoint(
    request,
    payload: dict = Body(...),  # noqa: B008  (Ninja DI pattern)
    dry_run: bool = Query(default=True),
):
    """POST /admin/import?dry_run=true.

    Body is the loose ``{"scenarios": ..., "slots": ...}`` payload returned
    by ``/admin/export``. ``dry_run=False`` REPLACES the catalog rows.

    Skips scenarios whose ``owner_user_id`` no longer maps to a real User
    (post-Phase-5 the column is FK-promoted) and reports the count.
    """
    require_superuser(request.auth)
    return ops_services.import_catalog(
        payload=payload,
        dry_run=dry_run,
        current_user=request.auth,
    )


# --------------------------------------------------------------------------
# /admin/retention
# --------------------------------------------------------------------------


@router.delete("/admin/retention", response=RetentionResult, tags=["admin"])
def admin_retention_endpoint(
    request,
    jobs_days: int | None = Query(default=None, ge=1),
    audit_days: int | None = Query(default=None, ge=1),
    graph_notifications_days: int | None = Query(default=None, ge=1),
):
    require_superuser(request.auth)
    return ops_services.prune_records(
        jobs_days=jobs_days,
        audit_days=audit_days,
        graph_notifications_days=graph_notifications_days,
        current_user=request.auth,
    )


# --------------------------------------------------------------------------
# /admin/settings (list + PUT + DELETE)
# --------------------------------------------------------------------------


@router.get("/admin/settings", response=AppSettingPage, tags=["admin"])
def admin_list_settings_endpoint(
    request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    require_superuser(request.auth)
    rows, total = ops_services.list_app_settings(limit=limit, offset=offset)
    return {
        "items": [ops_services.serialize_setting(record) for record in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.put("/admin/settings/{key}", response=AppSettingOut, tags=["admin"])
def admin_upsert_setting_endpoint(request, key: str, payload: AppSettingIn):
    require_superuser(request.auth)
    return ops_services.save_setting(
        key=key,
        value=payload.value,
        description=payload.description,
        current_user=request.auth,
    )


@router.delete("/admin/settings/{key}", tags=["admin"])
def admin_delete_setting_endpoint(request, key: str):
    require_superuser(request.auth)
    return ops_services.remove_setting(key=key, current_user=request.auth)


# --------------------------------------------------------------------------
# /audit
# --------------------------------------------------------------------------


@router.get("/audit", response=AuditPage, tags=["audit"])
def audit_log_endpoint(
    request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    actor_user_id: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    target_id: str | None = Query(default=None),
):
    """GET /audit -- newest first, all filters optional. Superuser only."""
    require_superuser(request.auth)
    rows = ops_services.list_audit(
        limit=limit,
        offset=offset,
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
    )
    total = ops_services.count_audit(
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
    )
    return {
        "items": [ops_services.serialize_audit(record) for record in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# --------------------------------------------------------------------------
# /artifacts (list + GET file + DELETE)
# --------------------------------------------------------------------------


@router.get("/artifacts", response=ArtifactPage, tags=["artifacts"])
def artifacts_list_endpoint(
    request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    require_superuser(request.auth)
    rows, total = ops_services.list_artifacts(limit=limit, offset=offset)
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/artifacts/{kind}/{name}", tags=["artifacts"])
def artifact_download_endpoint(request, kind: str, name: str):
    """GET /artifacts/{kind}/{name}.

    Streams the file via Django's :class:`FileResponse`. Path traversal
    protection lives inside :func:`ops.services.artifact_path` (rejects
    names containing ``/`` or ``\\\\``).
    """
    require_superuser(request.auth)
    path = ops_services.artifact_path(kind, name)
    return FileResponse(path.open("rb"), as_attachment=False, filename=path.name)


@router.delete("/artifacts", tags=["artifacts"])
def artifacts_prune_endpoint(
    request,
    older_than_days: int = Query(default=30, ge=1),
):
    require_superuser(request.auth)
    return ops_services.prune_artifacts(
        older_than_days=older_than_days,
        current_user=request.auth,
    )


# --------------------------------------------------------------------------
# /monitoring/summary + /metrics
# --------------------------------------------------------------------------


@router.get("/monitoring/summary", response=MonitoringSummary, tags=["monitoring"])
def monitoring_summary_endpoint(
    request,
    stuck_after_minutes: int = Query(default=30, ge=1),
    graph_expiring_hours: int = Query(default=24, ge=1),
):
    require_superuser(request.auth)
    return ops_services.monitoring_summary(
        stuck_after_minutes=stuck_after_minutes,
        graph_expiring_hours=graph_expiring_hours,
    )


@router.get("/metrics", tags=["monitoring"])
def metrics_endpoint(request):
    """GET /metrics -- Prometheus text exposition (text/plain; v=0.0.4)."""
    require_superuser(request.auth)
    text = ops_services.metrics_text()
    return HttpResponse(text, content_type="text/plain; version=0.0.4")
