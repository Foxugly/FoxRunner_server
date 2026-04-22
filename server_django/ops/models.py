"""Ops models — Job, JobEvent, AuditEntry, ExecutionHistory, AppSetting,
IdempotencyKey, GraphSubscription, GraphNotification.

Populated during phases 4–6. Declare every Index() from the migration
chain in ``Meta.indexes`` so ``makemigrations`` does not regenerate them
later.
"""

from __future__ import annotations
