from __future__ import annotations

import os
import sys

import httpx


def main() -> int:
    base_url = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    email = os.getenv("SMOKE_EMAIL")
    password = os.getenv("SMOKE_PASSWORD")
    timeout = float(os.getenv("SMOKE_TIMEOUT_SECONDS", "10"))

    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        for path in ("/api/v1/health", "/api/v1/ready", "/api/v1/version", "/api/v1/status"):
            response = client.get(path)
            response.raise_for_status()
            print(f"{path}: {response.status_code}")

        if email and password:
            token_response = client.post("/api/v1/auth/jwt/login", data={"username": email, "password": password})
            token_response.raise_for_status()
            token = token_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            for path in ("/api/v1/users/me", "/api/v1/config/client"):
                response = client.get(path, headers=headers)
                response.raise_for_status()
                print(f"{path}: {response.status_code}")
        else:
            print("auth smoke skipped: set SMOKE_EMAIL and SMOKE_PASSWORD to include login checks")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"smoke failed: {exc}", file=sys.stderr)
        raise
