from __future__ import annotations

import http.client
import json
import urllib.parse

import requests

from app.config import PushItConfig, PushoverConfig
from app.logger import Logger


class Notifier:
    def __init__(
        self,
        config: PushoverConfig | None,
        logger: Logger,
        pushit: PushItConfig | None = None,
    ):
        self.config = config
        self.log = logger
        self.pushit = pushit

    def is_enabled(self) -> bool:
        return self.config is not None or self.pushit is not None

    def send(self, message: str) -> bool:
        # PushIT first (our own platform). On success we're done; on failure we
        # fall back to Pushover so an outage on one channel still gets through.
        if self.pushit is not None and self._send_pushit(message):
            return True
        return self._send_pushover(message)

    def _send_pushit(self, message: str) -> bool:
        cfg = self.pushit
        url = cfg.base_url.rstrip("/") + "/notifications/app/send/"
        try:
            response = requests.post(
                url,
                json={"title": cfg.title, "message": message},
                headers={"X-App-Token": cfg.app_token},
                timeout=cfg.timeout_seconds,
            )
            response.raise_for_status()
            self.log.success("Notification PushIT envoyee.")
            return True
        except Exception as exc:
            self.log.error(f"Notification PushIT echouee: {exc}")
            return False

    def _send_pushover(self, message: str) -> bool:
        if self.config is None:
            if self.pushit is None:
                self.log.warning("Notification ignoree: aucun canal (PushIT/Pushover) configure.")
            return False

        payload = {
            "token": self.config.token,
            "user": self.config.user_key,
            "message": message,
            "sound": self.config.sound,
        }

        try:
            response = requests.post(
                "https://api.pushover.net/1/messages.json",
                data=payload,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            self.log.success("Notification envoyee.")
            return True
        except Exception as exc:
            self.log.error(f"Notification HTTP echouee: {exc}")

        try:
            conn = http.client.HTTPSConnection("api.pushover.net:443", timeout=self.config.timeout_seconds)
            conn.request(
                "POST",
                "/1/messages.json",
                urllib.parse.urlencode(payload),
                {"Content-type": "application/x-www-form-urlencoded"},
            )
            response = conn.getresponse()
            body = response.read().decode("utf-8", errors="replace")
            if 200 <= response.status < 300:
                self.log.success("Notification envoyee via fallback.")
                return True
            self.log.error(f"Notification fallback echouee: HTTP {response.status} {body}")
            return False
        except Exception as exc:
            self.log.error(f"Notification fallback echouee: {exc}")
            return False

    def send_json_debug(self, data: dict) -> None:
        self.log.debug(json.dumps(data, ensure_ascii=False))
