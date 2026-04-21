# Microsoft Graph

FoxRunner uses Microsoft Graph for password reset email and mailbox change notifications.

## Configuration

```env
GRAPH_MAIL_ENABLED=true
GRAPH_BASE_URL=https://graph.microsoft.com/v1.0
GRAPH_TENANT_ID=
GRAPH_CLIENT_ID=
GRAPH_CLIENT_SECRET=
GRAPH_MAIL_SENDER=
GRAPH_WEBHOOK_CLIENT_STATE=
APP_PASSWORD_RESET_URL=http://localhost:4200/reset-password
```

Required Entra application permissions depend on usage:

- `Mail.Send` application permission for sending password reset email.
- Mail read/change notification permissions for subscribed mailbox resources.

Admin consent is required for application permissions.

## Sending Mail

Password reset uses:

```text
POST /users/{GRAPH_MAIL_SENDER}/sendMail
```

If `GRAPH_MAIL_ENABLED=false`, FoxRunner falls back to SMTP configuration.

## Webhooks

Routes:

- `POST /graph/webhook`
- `POST /graph/lifecycle`
- `POST /graph/subscriptions`
- `GET /graph/subscriptions`
- `PATCH /graph/subscriptions/{subscription_id}`
- `DELETE /graph/subscriptions/{subscription_id}`
- `GET /graph/notifications`

Graph validates a webhook by calling it with `validationToken`. FoxRunner returns the token as `text/plain`.

Notifications are accepted only when `clientState` matches `GRAPH_WEBHOOK_CLIENT_STATE`, then persisted in `graph_notifications`.

Subscriptions created through `POST /graph/subscriptions` are persisted in `graph_subscriptions`. Renew and delete operations call Microsoft Graph first, then update the local database and audit log.

## Maintenance

Microsoft Graph subscriptions expire and must be renewed before `expiration_datetime`.

Use:

```text
PATCH /graph/subscriptions/{subscription_id}
```

Payload:

```json
{
  "expiration_datetime": "2026-04-22T12:00:00Z"
}
```

Webhook notifications can be inspected with `GET /graph/notifications?subscription_id=...`.

Automatic renewal is handled by Celery Beat through `api.tasks.renew_graph_subscriptions_task`.

Run beat:

```powershell
.\.venv\Scripts\celery.exe -A api.celery_app.celery_app beat --loglevel=INFO
```

Configuration:

```env
GRAPH_SUBSCRIPTION_RENEW_ENABLED=true
GRAPH_SUBSCRIPTION_RENEW_INTERVAL_SECONDS=3600
GRAPH_SUBSCRIPTION_RENEW_BEFORE_HOURS=24
GRAPH_SUBSCRIPTION_RENEW_EXTENSION_HOURS=48
```
