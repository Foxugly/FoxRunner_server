"""Integration tests for the Phase 7 admin / monitoring / audit / settings
/ artifacts endpoints.

Sixteen endpoints under ``/api/v1/`` (all superuser-only):

    GET    /admin/users
    PATCH  /admin/users/{target_user_id}
    GET    /admin/config-checks
    GET    /admin/db-stats
    GET    /admin/export
    POST   /admin/import?dry_run=
    DELETE /admin/retention
    GET    /admin/settings
    PUT    /admin/settings/{key}
    DELETE /admin/settings/{key}
    GET    /audit
    GET    /artifacts
    GET    /artifacts/{kind}/{name}
    DELETE /artifacts
    GET    /monitoring/summary
    GET    /metrics

Artifact tests use a tempfile directory injected via ``APP_ARTIFACTS_DIR``
(the service reads ``app.config.load_config`` at call time, so an env-var
override is sufficient to redirect the service at a controlled tree).
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from accounts.models import User
from catalog.models import Scenario, Slot
from django.test import Client, TestCase

from ops.models import (
    AppSetting,
    AuditEntry,
    GraphNotification,
    GraphSubscription,
    Job,
    JobEvent,
)


def _login(client: Client, email: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/jwt/login",
        data=f"username={email}&password={password}",
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 200, response.content
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


class _BaseAdminApiTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.alice = User.objects.create_user(email="alice@example.com", password="password123!")
        self.bob = User.objects.create_user(email="bob@example.com", password="password123!")
        self.admin = User.objects.create_superuser(email="admin@example.com", password="password123!")
        self.alice_token = _login(self.client, "alice@example.com", "password123!")
        self.admin_token = _login(self.client, "admin@example.com", "password123!")


class AdminPermissionsTest(_BaseAdminApiTest):
    """Every admin endpoint must reject non-superusers with 403."""

    def test_admin_endpoints_require_superuser(self):
        # Seed an AppSetting so the DELETE path has something to target.
        AppSetting.objects.create(key="foo", value={"k": 1})
        endpoints = [
            ("get", "/api/v1/admin/users"),
            ("patch", f"/api/v1/admin/users/{self.alice.id}"),
            ("get", "/api/v1/admin/config-checks"),
            ("get", "/api/v1/admin/db-stats"),
            ("get", "/api/v1/admin/export"),
            ("post", "/api/v1/admin/import?dry_run=true"),
            ("delete", "/api/v1/admin/retention"),
            ("get", "/api/v1/admin/settings"),
            ("put", "/api/v1/admin/settings/foo"),
            ("delete", "/api/v1/admin/settings/foo"),
            ("get", "/api/v1/audit"),
            ("get", "/api/v1/artifacts"),
            ("get", "/api/v1/artifacts/screenshots/nope.png"),
            ("delete", "/api/v1/artifacts"),
            ("get", "/api/v1/monitoring/summary"),
            ("get", "/api/v1/metrics"),
        ]
        bodies = {
            ("post", "/api/v1/admin/import?dry_run=true"): {"scenarios": {}, "slots": {}},
            ("put", "/api/v1/admin/settings/foo"): {"value": {}, "description": ""},
            ("patch", f"/api/v1/admin/users/{self.alice.id}"): {},
        }
        for method, path in endpoints:
            kwargs: dict = {}
            if method in {"post", "put", "patch"}:
                kwargs["data"] = json.dumps(bodies.get((method, path), {}))
                kwargs["content_type"] = "application/json"
            response = getattr(self.client, method)(path, **kwargs, **_auth(self.alice_token))
            self.assertEqual(response.status_code, 403, f"{method} {path}: {response.status_code} {response.content[:200]}")


class AdminListUsersTest(_BaseAdminApiTest):
    def test_admin_list_users_paginates(self):
        response = self.client.get("/api/v1/admin/users?limit=2&offset=0", **_auth(self.admin_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["limit"], 2)
        self.assertEqual(body["offset"], 0)
        self.assertEqual(len(body["items"]), 2)
        # Ordering by email: admin@, alice@, bob@
        self.assertEqual([item["email"] for item in body["items"]], ["admin@example.com", "alice@example.com"])

        page2 = self.client.get("/api/v1/admin/users?limit=2&offset=2", **_auth(self.admin_token))
        self.assertEqual(page2.status_code, 200)
        self.assertEqual([item["email"] for item in page2.json()["items"]], ["bob@example.com"])


class AdminUpdateUserTest(_BaseAdminApiTest):
    def test_admin_update_user_by_uuid_and_email(self):
        # By UUID
        response = self.client.patch(
            f"/api/v1/admin/users/{self.alice.id}",
            data=json.dumps({"is_verified": True, "timezone_name": "UTC"}),
            content_type="application/json",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.is_verified)
        self.assertEqual(self.alice.timezone_name, "UTC")
        self.assertTrue(AuditEntry.objects.filter(action="admin.update_user", target_id=str(self.alice.id)).exists())

        # By email
        response = self.client.patch(
            "/api/v1/admin/users/bob@example.com",
            data=json.dumps({"is_active": False}),
            content_type="application/json",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.bob.refresh_from_db()
        self.assertFalse(self.bob.is_active)

    def test_admin_update_user_unknown_404(self):
        response = self.client.patch(
            "/api/v1/admin/users/nope@example.com",
            data=json.dumps({"is_active": False}),
            content_type="application/json",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_admin_update_user_invalid_timezone_422(self):
        response = self.client.patch(
            f"/api/v1/admin/users/{self.alice.id}",
            data=json.dumps({"timezone_name": "Not/AZone"}),
            content_type="application/json",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 422, response.content)


class AdminConfigChecksTest(_BaseAdminApiTest):
    def test_admin_config_checks(self):
        response = self.client.get("/api/v1/admin/config-checks", **_auth(self.admin_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIn("status", body)
        self.assertIn("checks", body)
        self.assertIn("database", body["checks"])
        # Database reachable in test -> status ok.
        self.assertEqual(body["checks"]["database"], "ok")
        for key in (
            "auth_secret_configured",
            "database_url_configured",
            "celery_broker_url_configured",
            "celery_result_backend_configured",
            "scenarios_file_exists",
            "slots_file_exists",
            "artifacts_dir",
        ):
            self.assertIn(key, body["checks"], key)


class AdminDbStatsTest(_BaseAdminApiTest):
    def test_admin_db_stats(self):
        # Seed a few rows so the counters are non-trivial.
        Scenario.objects.create(scenario_id="sc-stats", owner=self.alice, definition={"steps": []})
        Job.objects.create(job_id="job-stats-1", user=self.alice, kind="run_scenario", target_id="sc-stats", status="failed", dry_run=True)
        GraphSubscription.objects.create(
            subscription_id="sub-expiring",
            resource="/me",
            expiration_datetime=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
        )
        response = self.client.get("/api/v1/admin/db-stats", **_auth(self.admin_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["tables"]["users"], 3)
        self.assertEqual(body["tables"]["scenarios"], 1)
        self.assertEqual(body["tables"]["jobs"], 1)
        self.assertEqual(body["failed_jobs"], 1)
        self.assertEqual(body["graph_subscriptions_expiring"], 1)


class AdminExportImportTest(_BaseAdminApiTest):
    def setUp(self):
        super().setUp()
        self.scenario = Scenario.objects.create(
            scenario_id="sc-a",
            owner=self.alice,
            description="original",
            definition={"description": "original", "user_id": str(self.alice.id), "steps": []},
        )
        Slot.objects.create(
            slot_id="slot-a",
            scenario=self.scenario,
            days=[1, 2],
            start="08:00",
            end="09:00",
            enabled=True,
        )

    def test_admin_export_returns_full_catalog(self):
        response = self.client.get("/api/v1/admin/export", **_auth(self.admin_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIn("sc-a", body["scenarios"]["scenarios"])
        slot_ids = {slot["id"] for slot in body["slots"]["slots"]}
        self.assertIn("slot-a", slot_ids)

    def test_admin_import_dry_run_returns_counts(self):
        payload = {
            "scenarios": {
                "schema_version": 1,
                "data": {},
                "scenarios": {"sc-b": {"description": "b", "user_id": str(self.alice.id), "steps": []}},
            },
            "slots": {"slots": [{"id": "slot-b", "days": [0], "start": "10:00", "end": "11:00", "scenario": "sc-b"}]},
        }
        response = self.client.post(
            "/api/v1/admin/import?dry_run=true",
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["dry_run"])
        self.assertEqual(body["scenarios"], 1)
        self.assertEqual(body["slots"], 1)
        # Dry run MUST NOT mutate the catalog.
        self.assertTrue(Scenario.objects.filter(scenario_id="sc-a").exists())
        self.assertFalse(Scenario.objects.filter(scenario_id="sc-b").exists())

    def test_admin_import_apply_replaces_catalog(self):
        payload = {
            "scenarios": {
                "schema_version": 1,
                "data": {},
                "scenarios": {"sc-b": {"description": "b", "user_id": str(self.alice.id), "steps": []}},
            },
            "slots": {"slots": [{"id": "slot-b", "days": [0], "start": "10:00", "end": "11:00", "scenario": "sc-b"}]},
        }
        response = self.client.post(
            "/api/v1/admin/import?dry_run=false",
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertFalse(body["dry_run"])
        self.assertTrue(body["imported"])
        self.assertEqual(body.get("skipped_scenarios"), 0)
        # sc-a replaced by sc-b.
        self.assertFalse(Scenario.objects.filter(scenario_id="sc-a").exists())
        self.assertTrue(Scenario.objects.filter(scenario_id="sc-b").exists())
        self.assertTrue(Slot.objects.filter(slot_id="slot-b").exists())
        self.assertTrue(AuditEntry.objects.filter(action="admin.import_catalog", target_type="catalog").exists())

    def test_admin_import_skips_invalid_owner(self):
        """Rows whose owner_user_id doesn't map to a real User are skipped
        AND counted (post-Phase-5 the FK would otherwise fail)."""
        payload = {
            "scenarios": {
                "schema_version": 1,
                "data": {},
                "scenarios": {
                    "sc-orphan": {"description": "orphan", "user_id": "default", "steps": []},
                    "sc-valid": {"description": "valid", "user_id": str(self.alice.id), "steps": []},
                },
            },
            "slots": {"slots": [{"id": "slot-valid", "days": [0], "start": "10:00", "end": "11:00", "scenario": "sc-valid"}]},
        }
        response = self.client.post(
            "/api/v1/admin/import?dry_run=false",
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body.get("skipped_scenarios"), 1)
        # Only the valid one made it into the DB.
        self.assertTrue(Scenario.objects.filter(scenario_id="sc-valid").exists())
        self.assertFalse(Scenario.objects.filter(scenario_id="sc-orphan").exists())

    def test_admin_import_invalid_payload_422(self):
        response = self.client.post(
            "/api/v1/admin/import?dry_run=true",
            data=json.dumps({"scenarios": "not-a-dict"}),
            content_type="application/json",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 422, response.content)


class AdminRetentionTest(_BaseAdminApiTest):
    def test_admin_retention_prunes_jobs_and_audit(self):
        # Old completed job (finished_at far in the past).
        old = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=120)
        recent = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
        Job.objects.create(
            job_id="old-job",
            user=self.alice,
            kind="run_scenario",
            target_id="sc",
            status="success",
            dry_run=True,
            finished_at=old,
        )
        JobEvent.objects.create(job_id="old-job", event_type="done", message="gone")
        Job.objects.create(
            job_id="new-job",
            user=self.alice,
            kind="run_scenario",
            target_id="sc",
            status="success",
            dry_run=True,
            finished_at=recent,
        )
        # Old audit row -- the created_at field is auto_now_add so we have
        # to bypass it with an UPDATE.
        entry = AuditEntry.objects.create(actor=self.alice, action="legacy", target_type="x", target_id="y")
        AuditEntry.objects.filter(pk=entry.pk).update(created_at=old)
        GraphNotification.objects.create(subscription_id="s1", change_type="created")
        gn = GraphNotification.objects.get(subscription_id="s1")
        GraphNotification.objects.filter(pk=gn.pk).update(created_at=old)

        response = self.client.delete(
            "/api/v1/admin/retention?jobs_days=30&audit_days=30&graph_notifications_days=30",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        removed = response.json()["removed"]
        self.assertEqual(removed["jobs"], 1)
        self.assertEqual(removed["job_events"], 1)
        self.assertEqual(removed["audit"], 1)
        self.assertEqual(removed["graph_notifications"], 1)
        self.assertFalse(Job.objects.filter(job_id="old-job").exists())
        self.assertTrue(Job.objects.filter(job_id="new-job").exists())
        # A new "admin.retention_prune" audit row was created by the action.
        self.assertTrue(AuditEntry.objects.filter(action="admin.retention_prune").exists())


class AdminSettingsCrudTest(_BaseAdminApiTest):
    def test_admin_settings_crud(self):
        # PUT upsert
        response = self.client.put(
            "/api/v1/admin/settings/ui.theme",
            data=json.dumps({"value": {"mode": "dark"}, "description": "UI theme"}),
            content_type="application/json",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["key"], "ui.theme")
        self.assertEqual(body["value"], {"mode": "dark"})
        self.assertEqual(body["description"], "UI theme")
        self.assertEqual(body["updated_by"], "admin@example.com")

        # GET list
        listing = self.client.get("/api/v1/admin/settings", **_auth(self.admin_token))
        self.assertEqual(listing.status_code, 200, listing.content)
        keys = [item["key"] for item in listing.json()["items"]]
        self.assertIn("ui.theme", keys)

        # Update via PUT again
        response = self.client.put(
            "/api/v1/admin/settings/ui.theme",
            data=json.dumps({"value": {"mode": "light"}, "description": "UI theme"}),
            content_type="application/json",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["value"], {"mode": "light"})

        # DELETE
        delete = self.client.delete("/api/v1/admin/settings/ui.theme", **_auth(self.admin_token))
        self.assertEqual(delete.status_code, 200, delete.content)
        self.assertEqual(delete.json(), {"deleted": "ui.theme"})
        self.assertFalse(AppSetting.objects.filter(key="ui.theme").exists())

        # DELETE missing -> 404
        missing = self.client.delete("/api/v1/admin/settings/ui.theme", **_auth(self.admin_token))
        self.assertEqual(missing.status_code, 404, missing.content)


class AuditEndpointTest(_BaseAdminApiTest):
    def test_audit_endpoint_filters(self):
        AuditEntry.objects.create(actor=self.alice, action="a", target_type="t1", target_id="x1")
        AuditEntry.objects.create(actor=self.admin, action="b", target_type="t2", target_id="x2")
        AuditEntry.objects.create(actor=self.alice, action="c", target_type="t1", target_id="x3")

        # No filter -> all 3, newest first.
        response = self.client.get("/api/v1/audit", **_auth(self.admin_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 3)
        self.assertEqual([item["action"] for item in body["items"]], ["c", "b", "a"])

        # Filter by actor
        response = self.client.get(
            f"/api/v1/audit?actor_user_id={self.alice.id}",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual({item["action"] for item in body["items"]}, {"a", "c"})

        # Filter by target_type
        response = self.client.get("/api/v1/audit?target_type=t2", **_auth(self.admin_token))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["action"], "b")

        # Filter by target_id
        response = self.client.get("/api/v1/audit?target_id=x3", **_auth(self.admin_token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)


class ArtifactsEndpointTest(_BaseAdminApiTest):
    """Artifacts tests use a tempdir + env-var override.

    ``ops.services._artifacts_dir`` reads ``app.config.load_config`` at
    call time, which picks up the ``APP_ARTIFACTS_DIR`` env var. ``with
    override_settings(...)`` is not enough on its own -- we patch the
    env var at test scope.
    """

    def setUp(self):
        super().setUp()
        self.tmpdir = Path(tempfile.mkdtemp(prefix="foxrunner-artifacts-"))
        (self.tmpdir / "screenshots").mkdir()
        (self.tmpdir / "pages").mkdir()
        (self.tmpdir / "screenshots" / "shot1.png").write_bytes(b"PNG-DATA")
        (self.tmpdir / "pages" / "page1.html").write_text("<html>", encoding="utf-8")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)
        super().tearDown()

    def _env(self):
        return mock.patch.dict("os.environ", {"APP_ARTIFACTS_DIR": str(self.tmpdir)})

    def test_artifacts_list_and_download(self):
        with self._env():
            response = self.client.get("/api/v1/artifacts", **_auth(self.admin_token))
            self.assertEqual(response.status_code, 200, response.content)
            body = response.json()
            self.assertEqual(body["total"], 2)
            names = {(item["kind"], item["name"]) for item in body["items"]}
            self.assertIn(("screenshots", "shot1.png"), names)
            self.assertIn(("pages", "page1.html"), names)

            download = self.client.get("/api/v1/artifacts/screenshots/shot1.png", **_auth(self.admin_token))
            self.assertEqual(download.status_code, 200)
            # FileResponse streams chunks -- join them for the assertion.
            streamed = b"".join(download.streaming_content)
            self.assertEqual(streamed, b"PNG-DATA")

    def test_artifacts_path_traversal_rejected(self):
        with self._env():
            bad = self.client.get("/api/v1/artifacts/screenshots/..%5Cshot1.png", **_auth(self.admin_token))
            # Depending on URL decoding, Django may resolve ``%5C`` to ``\`` --
            # the service rejects either form with 400. ``%2F`` would be a 404
            # because Ninja routes ``/`` as a path separator before we see it.
            self.assertIn(bad.status_code, (400, 404), bad.content)

            bad_kind = self.client.get("/api/v1/artifacts/not-a-kind/shot1.png", **_auth(self.admin_token))
            self.assertEqual(bad_kind.status_code, 404, bad_kind.content)

            missing = self.client.get("/api/v1/artifacts/screenshots/missing.png", **_auth(self.admin_token))
            self.assertEqual(missing.status_code, 404, missing.content)

    def test_artifacts_prune_removes_old(self):
        import os as _os

        old = self.tmpdir / "screenshots" / "shot1.png"
        # Backdate the file so it's older than the 1-day cutoff.
        old_time = (datetime.now(UTC) - timedelta(days=10)).timestamp()
        _os.utime(old, (old_time, old_time))
        with self._env():
            response = self.client.delete(
                "/api/v1/artifacts?older_than_days=1",
                **_auth(self.admin_token),
            )
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json(), {"removed": 1})
            self.assertFalse(old.exists())
            # The recent page1.html is still there.
            self.assertTrue((self.tmpdir / "pages" / "page1.html").exists())
        self.assertTrue(AuditEntry.objects.filter(action="artifacts.prune").exists())


class MonitoringSummaryTest(_BaseAdminApiTest):
    def test_monitoring_summary(self):
        # Build a handful of jobs across statuses.
        now = datetime.now(UTC).replace(tzinfo=None)
        Job.objects.create(job_id="j-q", user=self.alice, kind="run_scenario", target_id="sc", status="queued", dry_run=True)
        Job.objects.create(job_id="j-r", user=self.alice, kind="run_scenario", target_id="sc", status="running", dry_run=True)
        Job.objects.create(job_id="j-f", user=self.alice, kind="run_scenario", target_id="sc", status="failed", dry_run=True)
        completed = Job.objects.create(
            job_id="j-c",
            user=self.alice,
            kind="run_scenario",
            target_id="sc",
            status="success",
            dry_run=True,
        )
        Job.objects.filter(pk=completed.pk).update(started_at=now - timedelta(seconds=10), finished_at=now)
        response = self.client.get("/api/v1/monitoring/summary", **_auth(self.admin_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["jobs"]["total"], 4)
        self.assertEqual(body["jobs"]["queued"], 1)
        self.assertEqual(body["jobs"]["running"], 1)
        self.assertEqual(body["jobs"]["failed"], 1)
        self.assertIn("success", body["jobs"]["by_status"])
        # One completed job with a 10s duration.
        self.assertIsNotNone(body["jobs"]["average_duration_seconds"])
        self.assertAlmostEqual(body["jobs"]["average_duration_seconds"], 10.0, delta=0.5)
        self.assertEqual(body["graph"]["expiring_within_hours"], 24)


class MetricsEndpointTest(_BaseAdminApiTest):
    def test_metrics_returns_prometheus_text(self):
        Job.objects.create(job_id="jm", user=self.alice, kind="run_scenario", target_id="sc", status="failed", dry_run=True)
        response = self.client.get("/api/v1/metrics", **_auth(self.admin_token))
        self.assertEqual(response.status_code, 200, response.content[:120])
        self.assertIn("text/plain", response["Content-Type"])
        self.assertIn("version=0.0.4", response["Content-Type"])
        text = response.content.decode("utf-8")
        self.assertIn("foxrunner_jobs_total", text)
        self.assertIn("foxrunner_jobs_failed", text)
        self.assertIn("foxrunner_jobs_queued", text)
        self.assertIn("foxrunner_jobs_running", text)
        self.assertIn("foxrunner_jobs_stuck", text)
        self.assertIn("foxrunner_jobs_average_duration_seconds", text)
        self.assertIn("foxrunner_graph_subscriptions_expiring", text)
        # HELP/TYPE lines are correctly interleaved.
        self.assertIn("# TYPE foxrunner_jobs_total gauge", text)
