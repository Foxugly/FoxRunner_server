"""Tests for the global catalogue configuration endpoints (P3).

``GET/PUT /api/v1/catalog/config`` manage the ``data`` block of
``scenarios.json`` (pushovers/networks/defaults). They are superuser-only,
mask pushover secrets on read, treat them as write-only on update, and mirror
the ``CatalogConfig`` singleton into ``config/scenarios.json``.

Like ``test_json_sync``, the ``scenarios_file`` is redirected to a temp dir so
the real ``config/scenarios.json`` is never touched.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from django.test import Client, TestCase

from accounts.models import User
from app.config import load_config
from catalog.models import CatalogConfig
from catalog.services import MASKED_SECRET


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


class CatalogConfigApiTest(TestCase):
    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        tmp = Path(self.tmpdir.name)
        self.scenarios_path = tmp / "scenarios.json"
        real_config = load_config()
        patched = replace(
            real_config,
            runtime=replace(
                real_config.runtime,
                scenarios_file=self.scenarios_path,
                slots_file=tmp / "slots.json",
            ),
        )
        patcher = patch("catalog.services.load_config", return_value=patched)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Deterministic empty singleton (the migration may have seeded it).
        CatalogConfig.objects.all().delete()

        self.client = Client()
        self.admin = User.objects.create_superuser(
            email="admin@example.com", password="password123!"
        )
        self.bob = User.objects.create_user(email="bob@example.com", password="password123!")
        self.admin_token = _login(self.client, "admin@example.com", "password123!")
        self.bob_token = _login(self.client, "bob@example.com", "password123!")

    def _put(self, payload: dict, token: str):
        return self.client.put(
            "/api/v1/catalog/config",
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(token),
        )

    def test_get_and_put_require_superuser(self):
        self.assertEqual(
            self.client.get("/api/v1/catalog/config", **_auth(self.bob_token)).status_code,
            403,
        )
        self.assertEqual(self._put({}, self.bob_token).status_code, 403)

    def test_get_empty_defaults(self):
        response = self.client.get("/api/v1/catalog/config", **_auth(self.admin_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["pushovers"], {})
        self.assertEqual(body["networks"], {})
        self.assertEqual(body["default_pushover"], "")
        self.assertEqual(body["default_network"], "")

    def test_put_persists_masks_and_mirrors_to_file(self):
        payload = {
            "default_pushover": "main",
            "default_network": "office",
            "pushovers": {
                "main": {"token": "tok123", "user_key": "usr456", "sound": "vibrate"},
            },
            "networks": {"office": {"office_ipv4_networks": ["10.0.0.0/24"]}},
        }
        response = self._put(payload, self.admin_token)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()

        # Secrets are masked in the response, non-secret fields survive.
        self.assertEqual(body["pushovers"]["main"]["token"], MASKED_SECRET)
        self.assertEqual(body["pushovers"]["main"]["user_key"], MASKED_SECRET)
        self.assertEqual(body["pushovers"]["main"]["sound"], "vibrate")
        self.assertEqual(body["default_pushover"], "main")

        # Real secrets are stored in the DB.
        cfg = CatalogConfig.load()
        self.assertEqual(cfg.pushovers["main"]["token"], "tok123")
        self.assertEqual(cfg.pushovers["main"]["user_key"], "usr456")

        # Mirrored to scenarios.json with the real values (CLI runner input).
        document = json.loads(self.scenarios_path.read_text(encoding="utf-8"))
        self.assertEqual(document["data"]["pushovers"]["main"]["token"], "tok123")
        self.assertEqual(document["data"]["default_pushover"], "main")
        self.assertEqual(document["data"]["networks"]["office"]["office_ipv4_networks"], ["10.0.0.0/24"])

    def test_put_writeback_keeps_secret_when_masked_or_blank(self):
        CatalogConfig.objects.update_or_create(
            pk=1,
            defaults={
                "pushovers": {"main": {"token": "real-token", "user_key": "real-user", "sound": "vibrate"}},
                "networks": {},
                "default_pushover": "main",
                "default_network": "",
            },
        )
        payload = {
            "default_pushover": "main",
            "default_network": "",
            "pushovers": {
                # token echoed back masked -> keep stored; user_key replaced.
                "main": {"token": MASKED_SECRET, "user_key": "new-user", "sound": "pushover"},
            },
            "networks": {},
        }
        response = self._put(payload, self.admin_token)
        self.assertEqual(response.status_code, 200, response.content)

        cfg = CatalogConfig.load()
        self.assertEqual(cfg.pushovers["main"]["token"], "real-token")  # preserved
        self.assertEqual(cfg.pushovers["main"]["user_key"], "new-user")  # replaced
        self.assertEqual(cfg.pushovers["main"]["sound"], "pushover")

    def test_put_drops_mask_for_new_entry_without_prior_secret(self):
        # A brand-new pushover whose token is only the mask must NOT persist the
        # mask sentinel as a real credential.
        payload = {
            "default_pushover": "",
            "default_network": "",
            "pushovers": {"fresh": {"token": MASKED_SECRET, "sound": "vibrate"}},
            "networks": {},
        }
        response = self._put(payload, self.admin_token)
        self.assertEqual(response.status_code, 200, response.content)
        cfg = CatalogConfig.load()
        self.assertNotIn("token", cfg.pushovers["fresh"])
