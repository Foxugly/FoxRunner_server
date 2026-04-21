# Runbooks

## Redis Down

Symptoms:

- `/api/v1/ready` reports `redis: error`.
- New jobs cannot be submitted to Celery reliably.
- Workers may log broker connection failures.

Actions:

1. Check Redis process/container health.
2. Verify `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND`.
3. Restart Redis if the data loss profile is acceptable for your deployment.
4. Restart Celery workers after Redis is stable.
5. Confirm `/api/v1/ready` reports `redis: ok`.

## Celery Workers Missing

Symptoms:

- `/api/v1/ready` reports `celery: no_workers`.
- Jobs remain queued.
- `/api/v1/monitoring/summary` reports stuck jobs.

Actions:

1. Start or restart worker processes.
2. Check worker logs for import/config errors.
3. Confirm the worker uses the same broker URL as the API.
4. Confirm `/api/v1/ready` reports `celery: ok`.
5. Retry failed jobs with `POST /api/v1/jobs/{job_id}/retry?user_id=...` when appropriate.

## Graph Subscription Expired

Symptoms:

- No new Graph webhook notifications arrive.
- `/api/v1/monitoring/summary` reports subscriptions close to expiration or expired.
- Worker logs show renewal errors.

Actions:

1. Verify `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, and webhook URLs.
2. Check Azure app permissions and client secret expiration.
3. Renew subscriptions manually with `PATCH /api/v1/graph/subscriptions/{subscription_id}`.
4. If renewal fails, recreate the subscription.
5. Confirm Graph notifications are stored with `GET /api/v1/graph/notifications`.

## Jobs Stuck

Symptoms:

- `/api/v1/monitoring/summary` reports stuck queued/running jobs.
- Job events stop progressing.

Actions:

1. Check Celery worker process health.
2. Inspect job events with `GET /api/v1/jobs/{job_id}/events?user_id=...`.
3. Cancel stale jobs with `POST /api/v1/jobs/{job_id}/cancel?user_id=...`.
4. Retry if the underlying scenario is still valid.
5. Review screenshots/pages under `/api/v1/artifacts`.

## DB Migration Failed

Symptoms:

- Deployment fails during `alembic upgrade head`.
- API cannot start because tables/columns are missing.

Actions:

1. Stop API and workers before retrying schema changes.
2. Restore a recent DB backup if the migration partially applied and cannot be repaired.
3. Run `alembic current` and `alembic history`.
4. Test locally with `make migrate-test`.
5. Apply the fixed migration, then start API and workers.
