# Magic-link backend (FoxRunner_server) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add passwordless magic-link login to FoxRunner_server: `POST /api/v1/auth/magic-link/request` (email → emailed link, silent) and `POST /api/v1/auth/magic-link/exchange` (token → JWT), mirroring the existing password-reset pattern.

**Architecture:** A dedicated `TimestampSigner` token module (distinct salt, 15-min TTL) + two Ninja views in the existing auth router that reuse the login's `RefreshToken.for_user` issuance and the existing Graph/SMTP mailer. Eligibility = active AND verified. No new throttle code (the existing `RateLimitMiddleware` already covers `/auth/*`).

**Tech Stack:** Django + Django Ninja, `rest_framework_simplejwt`, `django.core.signing.TimestampSigner`, existing `app/mail.py` + `ops/graph.py`.

**Spec source:** `docs/superpowers/specs/2026-06-16-magic-link-backend-design.md`
**Branch:** `feat/magic-link` (already created from `main`; note: an unrelated uncommitted change to `foxrunner/middleware.py` may exist — leave it, don't stage it).
**Tests run with:** `python manage.py test <module> -v 2` from the repo root with the project venv active (`DJANGO_SETTINGS_MODULE=foxrunner.settings`). Mirror the existing `accounts/tests/test_auth.py`.

> **Note on rate limiting:** `foxrunner/rate_limit.py` `RateLimitMiddleware` already limits every `/auth/*` path (`_is_limited_path` → `startswith("/auth/")`), so the two new endpoints inherit per-IP rate limiting with **no new code**. If it trips during a test run (shared in-process window), set `API_RATE_LIMIT_ENABLED=false` for the test process.

---

## File Structure

- `accounts/magic_link.py` — **created**: token sign/parse (salt + TTL).
- `accounts/tests/test_magic_link.py` — **created**: token unit tests + endpoint integration tests.
- `foxrunner/serializers.py` — **modified**: `MagicLinkRequestIn`, `MagicLinkExchangeIn`.
- `app/mail.py` — **modified**: `send_magic_link_email`.
- `accounts/api.py` — **modified**: two views + imports/consts.

---

## Task 1: Token module + unit tests (TDD)

**Files:**
- Create: `accounts/magic_link.py`
- Create: `accounts/tests/test_magic_link.py`

- [ ] **Step 1: Write the failing token unit tests**

Create `accounts/tests/test_magic_link.py`:
```python
from __future__ import annotations

from unittest.mock import patch

from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from django.test import Client, TestCase

from accounts.magic_link import (
    MAGIC_LINK_MAX_AGE_SECONDS,
    make_magic_link_token,
    parse_magic_link_token,
)
from accounts.models import User


class MagicLinkTokenTest(TestCase):
    def test_round_trip(self):
        token = make_magic_link_token("abc-123")
        self.assertEqual(parse_magic_link_token(token), "abc-123")

    def test_expired_raises(self):
        token = make_magic_link_token("abc-123")
        original = signing.time.time
        try:
            signing.time.time = lambda: original() + MAGIC_LINK_MAX_AGE_SECONDS + 60
            with self.assertRaises(SignatureExpired):
                parse_magic_link_token(token)
        finally:
            signing.time.time = original

    def test_tampered_raises(self):
        with self.assertRaises(BadSignature):
            parse_magic_link_token("garbage:nope")
```

- [ ] **Step 2: Run the tests — must FAIL**

Run: `python manage.py test accounts.tests.test_magic_link -v 2`
Expected: FAIL (ImportError — `accounts.magic_link` does not exist).

- [ ] **Step 3: Implement the token module**

Create `accounts/magic_link.py`:
```python
"""Single-use magic-link token (mirror of the password-reset TimestampSigner).

A distinct salt isolates it from the password-reset namespace; single-use is
enforced by the 900s TTL on ``unsign`` (no DB tracking), matching the
password-reset contract.
"""

from __future__ import annotations

from django.core.signing import TimestampSigner

MAGIC_LINK_SALT = "accounts.magic_link"
MAGIC_LINK_MAX_AGE_SECONDS = 15 * 60  # 15 minutes


def make_magic_link_token(user_id) -> str:
    return TimestampSigner(salt=MAGIC_LINK_SALT).sign(str(user_id))


def parse_magic_link_token(token: str) -> str:
    """Return the embedded user_id (str). Raises SignatureExpired / BadSignature."""
    return TimestampSigner(salt=MAGIC_LINK_SALT).unsign(
        token, max_age=MAGIC_LINK_MAX_AGE_SECONDS
    )
```

- [ ] **Step 4: Run the tests — must PASS**

Run: `python manage.py test accounts.tests.test_magic_link -v 2`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add accounts/magic_link.py accounts/tests/test_magic_link.py
git commit -m "feat(auth): magic-link token module (TimestampSigner, 15-min TTL)"
```

---

## Task 2: Serializers

**Files:**
- Modify: `foxrunner/serializers.py`

- [ ] **Step 1: Add the two input schemas**

After `class ResetPasswordIn(Schema):` (which has `token: str` / `password: str`), add:
```python
class MagicLinkRequestIn(Schema):
    email: str


class MagicLinkExchangeIn(Schema):
    token: str
```
(The exchange response is an inline `dict[str, str]`, like `jwt_login` — no named output schema.)

- [ ] **Step 2: Verify it imports**

Run: `python -c "import django,os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','foxrunner.settings');django.setup();from foxrunner.serializers import MagicLinkRequestIn, MagicLinkExchangeIn;print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add foxrunner/serializers.py
git commit -m "feat(auth): magic-link request/exchange input schemas"
```

---

## Task 3: Mailer

**Files:**
- Modify: `app/mail.py`

- [ ] **Step 1: Add `send_magic_link_email`**

Append to `app/mail.py` (mirrors `send_password_reset_email`, but sends a **clickable link**):
```python
def send_magic_link_email(email: str, token: str) -> None:
    magic_url = os.getenv("APP_MAGIC_LINK_URL", "http://localhost:4200/auth/magic")
    link = f"{magic_url}/{token}"
    subject = "Votre lien de connexion FoxRunner"
    body = f"Cliquez sur ce lien pour vous connecter (valable 15 minutes) :\n\n{link}"

    if os.getenv("GRAPH_MAIL_ENABLED", "true").lower() == "true":
        send_graph_mail(to=email, subject=subject, body=body)
        return

    host = os.getenv("SMTP_HOST")
    if not host:
        logger.error("Magic-link email not sent: Graph is disabled and SMTP_HOST is not configured.")
        return
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM", username or "no-reply@localhost")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = email
    message.set_content(body)

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        if os.getenv("SMTP_STARTTLS", "true").lower() == "true":
            smtp.starttls()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)
```
New env var: `APP_MAGIC_LINK_URL` (default `http://localhost:4200/auth/magic`; in prod, point it at the deployed frontend — mirror of `APP_PASSWORD_RESET_URL`). The Graph credentials are the **same** ones password-reset already uses — nothing new to configure.

- [ ] **Step 2: Verify it imports**

Run: `python -c "from app.mail import send_magic_link_email;print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/mail.py
git commit -m "feat(auth): send_magic_link_email (Graph + SMTP fallback, clickable link)"
```

---

## Task 4: Endpoints + integration tests (TDD)

**Files:**
- Modify: `accounts/api.py`
- Modify: `accounts/tests/test_magic_link.py`

- [ ] **Step 1: Write the failing endpoint tests**

Append to `accounts/tests/test_magic_link.py`:
```python
class MagicLinkRequestEndpointTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email="eve@example.com", password="password123!", is_verified=True
        )

    def test_silent_for_unknown_email(self):
        with patch("app.mail.send_magic_link_email") as mock_send:
            r = self.client.post(
                "/api/v1/auth/magic-link/request",
                data={"email": "ghost@example.com"},
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 202, r.content)
        self.assertEqual(r.json(), {"status": "queued"})
        mock_send.assert_not_called()

    def test_silent_for_unverified(self):
        User.objects.create_user(
            email="unv@example.com", password="password123!", is_verified=False
        )
        with patch("app.mail.send_magic_link_email") as mock_send:
            r = self.client.post(
                "/api/v1/auth/magic-link/request",
                data={"email": "unv@example.com"},
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 202, r.content)
        mock_send.assert_not_called()

    def test_eligible_sends_token(self):
        with patch("app.mail.send_magic_link_email") as mock_send:
            r = self.client.post(
                "/api/v1/auth/magic-link/request",
                data={"email": "eve@example.com"},
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 202, r.content)
        mock_send.assert_called_once()
        args, _ = mock_send.call_args
        self.assertEqual(args[0], "eve@example.com")
        self.assertEqual(parse_magic_link_token(args[1]), str(self.user.id))


class MagicLinkExchangeEndpointTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email="frank@example.com", password="password123!", is_verified=True
        )

    def _exchange(self, token):
        return self.client.post(
            "/api/v1/auth/magic-link/exchange",
            data={"token": token},
            content_type="application/json",
        )

    def test_valid_token_returns_access_token(self):
        r = self._exchange(make_magic_link_token(self.user.id))
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body["access_token"])
        self.assertEqual(body["token_type"], "bearer")

    def test_expired_token_returns_410(self):
        token = make_magic_link_token(self.user.id)
        original = signing.time.time
        try:
            signing.time.time = lambda: original() + MAGIC_LINK_MAX_AGE_SECONDS + 60
            r = self._exchange(token)
        finally:
            signing.time.time = original
        self.assertEqual(r.status_code, 410, r.content)

    def test_invalid_token_returns_400(self):
        self.assertEqual(self._exchange("garbage:nope").status_code, 400)

    def test_unknown_user_returns_400(self):
        token = make_magic_link_token("00000000-0000-0000-0000-000000000000")
        self.assertEqual(self._exchange(token).status_code, 400)

    def test_unverified_user_returns_400(self):
        self.user.is_verified = False
        self.user.save(update_fields=["is_verified"])
        self.assertEqual(self._exchange(make_magic_link_token(self.user.id)).status_code, 400)
```

- [ ] **Step 2: Run — must FAIL**

Run: `python manage.py test accounts.tests.test_magic_link -v 2`
Expected: FAIL (404/errors — the endpoints don't exist yet). The token unit tests from Task 1 still pass.

- [ ] **Step 3: Implement the two endpoints**

In `accounts/api.py`: extend the serializer import and add the magic-link import near the top (after the existing `from foxrunner.serializers import ...` line):
```python
from foxrunner.serializers import (
    ForgotPasswordIn,
    MagicLinkExchangeIn,
    MagicLinkRequestIn,
    ResetPasswordIn,
    UserOut,
    UserPatchIn,
)
from accounts.magic_link import make_magic_link_token, parse_magic_link_token
```
Then add the two views (e.g. after `reset_password`):
```python
@router.post("/auth/magic-link/request", auth=None, response={202: dict})
def magic_link_request(request, payload: MagicLinkRequestIn):
    """Silent for unknown / ineligible emails (no enumeration)."""
    user = User.objects.filter(
        email__iexact=payload.email, is_active=True, is_verified=True
    ).first()
    if user is not None:
        token = make_magic_link_token(user.id)
        from app.mail import send_magic_link_email

        send_magic_link_email(user.email, token)
    return 202, {"status": "queued"}


@router.post("/auth/magic-link/exchange", auth=None)
def magic_link_exchange(request, payload: MagicLinkExchangeIn) -> dict[str, str]:
    """Exchange a magic-link token for a JWT. 410 expired, 400 invalid."""
    try:
        user_id = parse_magic_link_token(payload.token)
    except SignatureExpired:
        raise HttpError(410, "Lien expire.") from None
    except BadSignature:
        raise HttpError(400, "Lien invalide.") from None

    try:
        user = User.objects.get(id=user_id)
    except (User.DoesNotExist, ValueError):
        raise HttpError(400, "Lien invalide.") from None

    if not (user.is_active and user.is_verified):
        raise HttpError(400, "Lien invalide.") from None

    refresh = RefreshToken.for_user(user)
    return {"access_token": str(refresh.access_token), "token_type": "bearer"}
```
(`SignatureExpired`, `BadSignature`, `HttpError`, `RefreshToken`, `User` are already imported in `accounts/api.py`.)

- [ ] **Step 4: Run — must PASS**

Run: `python manage.py test accounts.tests.test_magic_link -v 2`
Expected: PASS (3 token + 3 request + 5 exchange = 11 tests). If a `/auth/` rate-limit trips, re-run with `API_RATE_LIMIT_ENABLED=false`.

- [ ] **Step 5: Commit**

```bash
git add accounts/api.py accounts/tests/test_magic_link.py
git commit -m "feat(auth): magic-link request + exchange endpoints"
```

---

## Task 5: Full suite + OpenAPI + finalize

**Files:** none (verification) — optional OpenAPI export

- [ ] **Step 1: Run the whole auth + magic-link suite**

Run: `python manage.py test accounts -v 2`
Expected: all existing auth tests + the 11 new magic-link tests pass.

- [ ] **Step 2: (If the repo commits a generated OpenAPI) refresh it**

If `scripts/export_openapi.py` produces a committed `openapi.json`, run it so the new endpoints appear (the frontend consumes this via `gen:api`):
Run: `python scripts/export_openapi.py` (or the `Makefile` target if one exists, e.g. `make openapi`).
Commit the regenerated file if one changed. If the repo does not commit a spec file, skip.

- [ ] **Step 3: Commit any spec/export change**

```bash
git add -A
git commit -m "chore(openapi): expose magic-link endpoints in the API schema"
```
(Skip if nothing changed.)

---

## Self-Review (done at writing)

- **Spec coverage:** §contrat request/exchange (T4), token module 15-min TTL + salt (T1), serializers (T2), mailer Graph+SMTP clickable link (T3), eligibility active+verified gated in both views + tests (T4), 410/400 distinction (T4), anti-enumeration 202 (T4). Throttle = inherited from existing `/auth/*` middleware (noted, no task). ✓
- **Placeholders:** none — full code for module, serializers, mailer, endpoints, and 11 tests; exact `manage.py test` commands. ✓
- **Type/name consistency:** `make_magic_link_token` / `parse_magic_link_token` / `MAGIC_LINK_MAX_AGE_SECONDS` / `MagicLinkRequestIn` / `MagicLinkExchangeIn` / `send_magic_link_email` used identically across T1–T4; response shape `{access_token, token_type}` matches `jwt_login`. `create_user(..., is_verified=True)` used for eligible test users (the manager defaults it False). ✓
