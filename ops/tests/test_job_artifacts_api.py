from __future__ import annotations

from django.test import Client, TestCase

from accounts.models import User
from app.config import load_config
from ops.models import Job


class JobArtifactsApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(email="o@x.co", password="pw12345!")
        self.other = User.objects.create_user(email="z@x.co", password="pw12345!")
        self.job = Job.objects.create(job_id="jA", user=self.owner, kind="run_scenario", target_id="scn", status="failed", dry_run=False)
        shots = load_config().runtime.artifacts_dir / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        (shots / "jA.png").write_bytes(b"\x89PNG\r\n\x1a\n")

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
