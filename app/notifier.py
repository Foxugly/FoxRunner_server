from __future__ import annotations

import http.client
import json
import urllib.parse

import requests

from app.config import PushoverConfig
from app.logger import Logger


class Notifier:
    def __init__(self, config: PushoverConfig | None, logger: Logger):
        self.config = config
        self.log = logger

    def is_enabled(self) -> bool:
        return self.config is not None

    def send(self, message: str) -> bool:
        if self.config is None:
            self.log.warning("Notification ignoree: configuration Pushover absente.")
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
