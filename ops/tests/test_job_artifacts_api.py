from __future__ import annotations

import shutil

from django.test import Client, TestCase

from accounts.models import User
from app.config import load_config
from ops.models import Job


class JobArtifactsApiTests(TestCase):
    def setUp(self):
        # Clean up any leftover artifacts from previous test runs
        artifacts_dir = load_config().runtime.artifacts_dir
        if artifacts_dir.exists():
            shutil.rmtree(artifacts_dir)

        self.client = Client()
        self.owner = User.objects.create_user(email="o@x.co", password="pw12345!")
        self.other = User.objects.create_user(email="z@x.co", password="pw12345!")
        self.job = Job.objects.create(job_id="jA", user=self.owner, kind="run_scenario", target_id="scn", status="failed", dry_run=False)
        shots = load_config().runtime.artifacts_dir / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        (shots / "jA.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def tearDown(self):
        # Clean up filesystem artifacts after each test to prevent leakage
        artifacts_dir = load_config().runtime.artifacts_dir
        if artifacts_dir.exists():
            shutil.rmtree(artifacts_dir)

    def _token(self, email):
        r = self.client.post("/api/v1/auth/jwt/login", data=f"username={email}&password=pw12345!", content_type="application/x-www-form-urlencoded")
        return r.json()["access_token"]

    def test_owner_gets_screenshot(self):
        r = self.client.get("/api/v1/jobs/jA/artifacts/screenshot", HTTP_AUTHORIZATION=f"Bearer {self._token('o@x.co')}")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r["Content-Type"], "image/png")

    def test_other_user_forbidden(self):
        r = self.client.get("/api/v1/jobs/jA/artifacts/screenshot", HTTP_AUTHORIZATION=f"Bearer {self._token('z@x.co')}")
        self.assertEqual(r.status_code, 403)

    def test_unknown_kind_returns_404(self):
        r = self.client.get("/api/v1/jobs/jA/artifacts/bogus", HTTP_AUTHORIZATION=f"Bearer {self._token('o@x.co')}")
        self.assertEqual(r.status_code, 404)

    def test_missing_file_returns_404(self):
        r = self.client.get("/api/v1/jobs/jA/artifacts/page_source", HTTP_AUTHORIZATION=f"Bearer {self._token('o@x.co')}")
        self.assertEqual(r.status_code, 404)

    def test_page_source_served_with_html_content_type(self):
        from app.config import load_config

        pages = load_config().runtime.artifacts_dir / "pages"
        pages.mkdir(parents=True, exist_ok=True)
        (pages / "jA.html").write_text("<html></html>", encoding="utf-8")
        r = self.client.get("/api/v1/jobs/jA/artifacts/page_source", HTTP_AUTHORIZATION=f"Bearer {self._token('o@x.co')}")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn("text/html", r["Content-Type"])
