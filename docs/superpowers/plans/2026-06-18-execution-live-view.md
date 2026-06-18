# Execution Live View — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Launch a scenario from the UI and watch its steps run live (checklist + progress + plain-French labels + failure card with inline screenshot), powered by per-step events written to the DB.

**Architecture:** A framework-agnostic event sink in the CLI engine (`scenarios/runner.py`) emits one event per top-level step (started/succeeded/failed/skipped) with a stable `step_id` and a traceback on failure. A job dispatcher runs the scenario inline in a background thread when no Celery worker is detected (auto), else via Celery; both paths funnel the sink into `JobEvent` rows. The frontend builds a checklist from the scenario definition and overlays live status by polling `GET /jobs/{id}/events` (~1.5s), with a user-scoped route serving the failure screenshot.

**Tech Stack:** Django 6 + django-ninja, Celery, Selenium engine (pure Python); Angular 21 (FoxRunner_frontend) + Angular 19/PrimeNG 19 (FoxRunner_frontend_node20), vitest.

**Spec:** `docs/superpowers/specs/2026-06-18-execution-live-view-design.md`

**Branch:** `feat/execution-live-view` (already created from `main`).

---

## File Structure

**Backend (`D:\PycharmProjects\FoxRunner_server`)**
- Create `scenarios/events.py` — `StepEvent` dataclass + `StepEventSink` type + `step_label_payload` helper. One responsibility: the event contract.
- Modify `scenarios/runner.py` — accept `on_event` sink; emit per top-level step in `run_task` + `_execute_hook_steps`; carry artifact paths on failure.
- Create `ops/runner_bridge.py` — `execute_scenario_job(job_id, scenario_id, dry_run)`: shared driver used by both Celery and inline paths; wires the sink → `append_job_event`.
- Modify `ops/services.py` — `enqueue_scenario_job` gains auto inline/Celery dispatch (`_celery_worker_available()` + `_run_job_inline()`); `serialize_job_event` unchanged (payload already passthrough).
- Modify `ops/tasks.py` — `run_scenario_job` delegates to `ops.runner_bridge.execute_scenario_job`.
- Modify `ops/api.py` — add `GET /jobs/{job_id}/artifacts/{kind}` (owner-scoped file response).
- Modify `foxrunner/settings.py` — read `RUN_JOBS_INLINE` (`auto|true|false`, default `auto`).
- Modify `.env.example` — add `RUN_JOBS_INLINE=auto`.
- Test: `tests/test_step_events.py` (CLI engine), `ops/tests/test_runner_bridge.py`, `ops/tests/test_job_artifacts_api.py`.

**Frontend A21 (`C:\Users\Renaud\WebstormProjects\FoxRunner_frontend`)**
- Create `src/app/core/api/step-label.ts` — `stepLabel(step)` FR label helper + `STEP_ID` builder.
- Create `src/app/core/api/step-label.spec.ts` — vitest for labels + step-id.
- Modify `src/app/core/api/jobs.service.ts` — add `artifactUrl(jobId, kind)`.
- Modify `src/app/core/api/types.ts` — add execution-view domain aliases.
- Rewrite `src/app/features/jobs/detail/job-detail.component.ts` — execution view (header+progress, checklist, failure card, relaunch).
- Modify `src/app/features/scenarios/detail/*.ts` — add "Lancer (dry-run)/(réel)" buttons → create job → navigate.
- Modify `src/app/core/api/schema.ts` — regenerated (artifacts route).

**Frontend node20 (`C:\Users\Renaud\WebstormProjects\FoxRunner_frontend_node20`)**
- Same files, Angular 19 / PrimeNG 19 deltas (see Task 12).

---

## Phase A — Engine event sink

### Task 1: StepEvent contract

**Files:**
- Create: `scenarios/events.py`
- Test: `tests/test_step_events.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_step_events.py
import unittest

from scenarios.events import StepEvent


class StepEventTests(unittest.TestCase):
    def test_step_event_defaults(self):
        ev = StepEvent(step_id="steps[0]", event_type="step_started", step_type="open_url")
        self.assertEqual(ev.level, "info")
        self.assertEqual(ev.message, "")
        self.assertIsNone(ev.traceback)
        self.assertEqual(ev.payload, {})

    def test_failed_event_carries_traceback_and_level(self):
        ev = StepEvent(
            step_id="steps[2]",
            event_type="step_failed",
            step_type="wait_for_element",
            level="error",
            message="boom",
            traceback="Traceback…",
            payload={"screenshot": "abc.png"},
        )
        self.assertEqual(ev.level, "error")
        self.assertEqual(ev.payload["screenshot"], "abc.png")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_step_events -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scenarios.events'`

- [ ] **Step 3: Write minimal implementation**

```python
# scenarios/events.py
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StepEvent:
    """One per-step signal emitted by the engine to an optional sink.

    Framework-agnostic: the CLI engine has no Django dependency. The job
    layer adapts these into ``JobEvent`` rows.
    """

    step_id: str
    event_type: str  # step_started | step_succeeded | step_failed | step_skipped
    step_type: str
    level: str = "info"
    message: str = ""
    traceback: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    duration_ms: int | None = None


# A sink consumes StepEvents. None == no observation (default CLI behaviour).
StepEventSink = Callable[[StepEvent], None]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_step_events -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add scenarios/events.py tests/test_step_events.py
git commit -m "feat(engine): StepEvent contract for per-step observation"
```

---

### Task 2: Emit per-step events from run_task

**Files:**
- Modify: `scenarios/runner.py`
- Test: `tests/test_step_events.py` (extend)

Design: `run_task` gains `on_event: StepEventSink | None = None`. A stable
`step_id` is `"{collection}[{index}]"` (e.g. `steps[0]`, `before_steps[1]`).
Events are emitted around each **top-level** step in `scenario.steps` and in
each hook collection. Skips use the pure `_should_execute`. Nested blocks
keep their top-level id (no inner tracing in v1). Retry events are deferred
to a later iteration (documented). The sink must never break the run.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_step_events.py  (append)
from app.config import TaskConfig
from app.logger import Logger
from scenarios.events import StepEvent
from scenarios.loader import ScenarioData, ScenarioDefinition, ScenarioStep
from scenarios.runner import run_task


def _scn(steps):
    return ScenarioDefinition(scenario_id="s", description="", steps=tuple(steps))


class RunTaskEventTests(unittest.TestCase):
    def setUp(self):
        self.events: list[StepEvent] = []
        self.cfg = TaskConfig()
        self.data = ScenarioData(pushovers={}, networks={})

    def _run(self, scn):
        return run_task(
            self.cfg, Logger(debug_enabled=False), scenario=scn, scenario_data=self.data,
            dry_run=True, on_event=self.events.append,
        )

    def test_emits_started_then_succeeded_per_step(self):
        scn = _scn([ScenarioStep(type="notify", payload={"message": "hi"})])
        result = self._run(scn)
        self.assertTrue(result.success)
        kinds = [(e.step_id, e.event_type) for e in self.events]
        self.assertIn(("steps[0]", "step_started"), kinds)
        self.assertIn(("steps[0]", "step_succeeded"), kinds)

    def test_skipped_when_condition_false(self):
        scn = _scn([ScenarioStep(type="notify", payload={"message": "x"}, when="context_exists:never")])
        self._run(scn)
        kinds = [e.event_type for e in self.events if e.step_id == "steps[0]"]
        self.assertIn("step_skipped", kinds)
        self.assertNotIn("step_succeeded", kinds)

    def test_failed_carries_traceback(self):
        # http_request to an invalid scheme raises inside the handler (non-dry path
        # uses real ops); use a guaranteed-failing assert via unknown step is not
        # possible, so force failure with a bad notify payload key.
        scn = _scn([ScenarioStep(type="format_context", payload={"key": "k"})])  # missing 'template' -> KeyError
        result = self._run(scn)
        self.assertFalse(result.success)
        failed = [e for e in self.events if e.event_type == "step_failed"]
        self.assertTrue(failed)
        self.assertTrue(failed[0].traceback)
```

> Note: if `format_context` validation makes that payload raise at load
> time instead of run time, substitute any step whose handler raises in
> dry-run; confirm by running the test and reading the failure.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_step_events.RunTaskEventTests -v`
Expected: FAIL — `run_task() got an unexpected keyword argument 'on_event'`

- [ ] **Step 3: Implement the sink in `scenarios/runner.py`**

Add import at top:

```python
from scenarios.events import StepEvent, StepEventSink
```

Add a helper near the top of the module:

```python
def _emit(on_event: StepEventSink | None, **kwargs) -> None:
    """Best-effort sink call — a sink error must never break the run."""
    if on_event is None:
        return
    try:
        on_event(StepEvent(**kwargs))
    except Exception:  # pragma: no cover - defensive
        pass
```

Change `run_task(...)` signature to add `on_event: StepEventSink | None = None`
(append as last keyword param). Replace the top-level `steps` loop body so each
step is wrapped:

```python
        for index, step in enumerate(scenario.steps, start=1):
            step_id = f"steps[{index - 1}]"
            current_step = f"{index}:{step.type}"
            if not _should_execute(step, context):
                _emit(on_event, step_id=step_id, event_type="step_skipped", step_type=step.type,
                      message="Étape ignorée (condition when=faux).")
                continue
            _emit(on_event, step_id=step_id, event_type="step_started", step_type=step.type)
            started = time.monotonic()
            if not dry_run and _requires_driver(step.type) and driver is None:
                driver = create_driver(config)
            try:
                driver = _execute_scenario_step(
                    step, operation_registry=operation_registry, driver=driver, config=config,
                    logger=logger, notifier=notifier, network_check=network_check,
                    network_check_by_key=network_check_by_key, scenario_data=scenario_data,
                    context=context, dry_run=dry_run, parallel_safe_steps=parallel_safe_steps,
                )
            except Exception:
                _emit(on_event, step_id=step_id, event_type="step_failed", step_type=step.type,
                      level="error", message=context.get("error_message", ""),
                      traceback=traceback.format_exc(),
                      duration_ms=int((time.monotonic() - started) * 1000))
                raise
            _emit(on_event, step_id=step_id, event_type="step_succeeded", step_type=step.type,
                  duration_ms=int((time.monotonic() - started) * 1000))
```

`traceback` and `time` are already imported in `runner.py`? `time` yes,
`traceback` no — add `import traceback` at top.

> The outer `try/except` in `run_task` still captures the failing
> `current_step` and builds the `TaskRunResult` with screenshot/page_source
> as today; the per-step `step_failed` event above fires first (re-raise
> propagates to the existing handler).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_step_events -v`
Expected: PASS. If the chosen failing-step doesn't raise in dry-run, adjust the test step type and re-run.

- [ ] **Step 5: Run the full CLI suite (no regressions)**

Run: `python -m unittest discover -s tests`
Expected: OK (existing 78 + new).

- [ ] **Step 6: Commit**

```bash
git add scenarios/runner.py tests/test_step_events.py
git commit -m "feat(engine): emit per-step events (started/succeeded/failed/skipped) with traceback"
```

---

### Task 3: Emit events for hook collections + artifact refs on failure

**Files:**
- Modify: `scenarios/runner.py`
- Test: `tests/test_step_events.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_step_events.py (append to RunTaskEventTests)
    def test_before_steps_emit_with_collection_id(self):
        scn = ScenarioDefinition(
            scenario_id="s", description="",
            before_steps=(ScenarioStep(type="notify", payload={"message": "b"}),),
            steps=(ScenarioStep(type="notify", payload={"message": "s"}),),
        )
        self._run(scn)
        ids = {e.step_id for e in self.events}
        self.assertIn("before_steps[0]", ids)
        self.assertIn("steps[0]", ids)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_step_events.RunTaskEventTests.test_before_steps_emit_with_collection_id -v`
Expected: FAIL — `before_steps[0]` not in ids (hooks don't emit yet).

- [ ] **Step 3: Thread the sink + collection name through `_execute_hook_steps`**

Add params `on_event` and `collection: str` to `_execute_hook_steps`, and emit
per child exactly like Task 2 (compute `step_id = f"{collection}[{i}]"`). Pass
`on_event` from every `_execute_hook_steps(...)` call site in `run_task`
(`before_steps` → `"before_steps"`, `on_success_steps` → `"on_success"`,
`on_failure_steps` → `"on_failure"`, `finally_steps` → `"finally_steps"`).
Wrap each child with started/skipped/succeeded/failed using the same `_emit`
pattern. On the failure path in `run_task`, after computing
`screenshot_path`/`page_source_path`, they are already attached to
`TaskRunResult`; ALSO include them in the `step_failed` payload by storing them
in `context["__artifacts__"] = {"screenshot": ..., "page_source": ...}` before
re-raising so the sink (Task 2 failure emit) can read them:

```python
        except Exception:
            arts = context.get("__artifacts__") or {}
            _emit(on_event, step_id=step_id, event_type="step_failed", step_type=step.type,
                  level="error", message=context.get("error_message", ""),
                  traceback=traceback.format_exc(),
                  payload={k: v for k, v in arts.items() if v},
                  duration_ms=int((time.monotonic() - started) * 1000))
            raise
```

In the `run_task` outer `except`, set `context["__artifacts__"]` from
`screenshot_path`/`page_source_path` (store just the file name via
`os.path.basename`) — but note the top-level `step_failed` already fired before
the outer handler. To keep artifacts on the event, capture them inside the
per-step `except` instead: compute screenshot/page-source there is too invasive;
v1 keeps artifacts on the **outer** failure and the frontend reads them from the
`job.result`/final `failed` event (see Task 5). Therefore: in this task only add
hook emission; artifact wiring lands in Task 5 at the job layer.

> Decision: artifact references are attached at the **job layer** (Task 5),
> not the engine, to avoid threading file paths through the engine. Remove the
> `payload` artifact bit above from the engine; keep engine events
> artifact-free.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_step_events -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scenarios/runner.py tests/test_step_events.py
git commit -m "feat(engine): emit per-step events for hook collections"
```

---

## Phase B — Job dispatch (auto inline/Celery)

### Task 4: Shared job runner bridge

**Files:**
- Create: `ops/runner_bridge.py`
- Test: `ops/tests/test_runner_bridge.py`

The bridge runs a scenario for a job, translating `StepEvent`s into
`JobEvent` rows and updating the `Job`. Used by both Celery and inline paths.

- [ ] **Step 1: Write the failing test**

```python
# ops/tests/test_runner_bridge.py
from __future__ import annotations

from unittest import mock

from django.test import TestCase

from accounts.models import User
from ops.models import Job, JobEvent
from ops.runner_bridge import execute_scenario_job


class RunnerBridgeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="a@b.co", password="pw12345!")
        self.job = Job.objects.create(job_id="j1", user=self.user, kind="run_scenario",
                                      target_id="scn", status="queued", dry_run=True)

    def test_success_writes_step_events_and_marks_success(self):
        from scenarios.events import StepEvent

        def fake_run(scenario_id, dry_run, on_event=None):
            on_event(StepEvent(step_id="steps[0]", event_type="step_started", step_type="open_url"))
            on_event(StepEvent(step_id="steps[0]", event_type="step_succeeded", step_type="open_url", duration_ms=12))
            return 0

        with mock.patch("ops.runner_bridge._run_scenario_with_sink", side_effect=fake_run):
            execute_scenario_job("j1", "scn", True)

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "success")
        types = list(JobEvent.objects.filter(job_id="j1").values_list("event_type", flat=True))
        self.assertIn("step_started", types)
        self.assertIn("step_succeeded", types)

    def test_failure_records_traceback_in_job_error(self):
        def boom(scenario_id, dry_run, on_event=None):
            raise RuntimeError("kaboom")

        with mock.patch("ops.runner_bridge._run_scenario_with_sink", side_effect=boom):
            with self.assertRaises(RuntimeError):
                execute_scenario_job("j1", "scn", True)

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "failed")
        self.assertIn("kaboom", self.job.error or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test ops.tests.test_runner_bridge -v 1`
Expected: FAIL — `No module named 'ops.runner_bridge'`

- [ ] **Step 3: Implement `ops/runner_bridge.py`**

```python
"""Shared scenario-job runner used by both the Celery task and the inline
dispatcher. Translates engine StepEvents into JobEvent rows and updates the
Job row. The DB is the single source of truth the frontend polls."""

from __future__ import annotations

import traceback
from datetime import UTC, datetime

from scenarios.events import StepEvent


def _run_scenario_with_sink(scenario_id: str, dry_run: bool, on_event) -> int:
    """Build the DB-backed scheduler service and run one scenario with a sink.

    Kept as a tiny seam so tests can patch it without spinning Selenium.
    """
    from catalog.services import build_service_from_db

    service = build_service_from_db()
    return service.run_scenario(scenario_id, dry_run=dry_run, on_event=on_event)


def execute_scenario_job(job_id: str, scenario_id: str, dry_run: bool) -> dict:
    from ops.models import Job
    from ops.services import append_job_event

    record = Job.objects.get(job_id=job_id)
    record.status = "running"
    record.started_at = datetime.now(UTC)
    record.save(update_fields=["status", "started_at", "updated_at"])
    append_job_event(job_id=job_id, event_type="running", message="Exécution démarrée.")

    def sink(ev: StepEvent) -> None:
        append_job_event(
            job_id=job_id,
            event_type=ev.event_type,
            level=ev.level,
            message=ev.message,
            step=ev.step_id,
            payload={"step_type": ev.step_type, "duration_ms": ev.duration_ms,
                     "traceback": ev.traceback, **(ev.payload or {})},
        )

    try:
        exit_code = _run_scenario_with_sink(scenario_id, dry_run, sink)
        record.refresh_from_db()
        record.status = "success" if exit_code == 0 else "failed"
        record.exit_code = exit_code
        record.finished_at = datetime.now(UTC)
        record.result = {"scenario_id": scenario_id, "dry_run": dry_run}
        record.save(update_fields=["status", "exit_code", "finished_at", "result", "updated_at"])
        append_job_event(job_id=job_id, event_type=record.status,
                         level="info" if exit_code == 0 else "error",
                         message=f"Exécution terminée (exit_code={exit_code}).",
                         payload={"exit_code": exit_code})
        return {"job_id": job_id, "exit_code": exit_code}
    except Exception as exc:
        record.refresh_from_db()
        record.status = "failed"
        record.error = traceback.format_exc()
        record.finished_at = datetime.now(UTC)
        record.save(update_fields=["status", "error", "finished_at", "updated_at"])
        append_job_event(job_id=job_id, event_type="failed", level="error", message=str(exc))
        raise
```

- [ ] **Step 4: Add `on_event` passthrough to `SchedulerService.run_scenario`**

In `scheduler/service.py`, `run_scenario(self, scenario_id, dry_run)` →
`run_scenario(self, scenario_id, dry_run, on_event=None)`, and pass
`on_event=on_event` into the `run_task(...)` call inside `_run_once`. Thread an
`on_event` param through `_run_once` (default `None`). Existing callers
(`run_slot`, `loop`, etc.) pass nothing → unchanged behaviour.

- [ ] **Step 5: Run test to verify it passes**

Run: `python manage.py test ops.tests.test_runner_bridge -v 1`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add ops/runner_bridge.py ops/tests/test_runner_bridge.py scheduler/service.py
git commit -m "feat(jobs): shared runner bridge translating step events to JobEvents"
```

---

### Task 5: Auto inline/Celery dispatch + artifact refs

**Files:**
- Modify: `ops/services.py` (`enqueue_scenario_job`, add `_celery_worker_available`, `_run_job_inline`)
- Modify: `ops/tasks.py` (`run_scenario_job` delegates to the bridge)
- Modify: `foxrunner/settings.py` (`RUN_JOBS_INLINE`)
- Modify: `.env.example`
- Test: `ops/tests/test_runner_bridge.py` (extend) / `ops/tests/test_jobs_api.py`

- [ ] **Step 1: Write the failing test**

```python
# ops/tests/test_runner_bridge.py (append)
from unittest import mock

from ops.services import _celery_worker_available


class DispatchTests(TestCase):
    @mock.patch("ops.services._celery_worker_available", return_value=False)
    @mock.patch("ops.services._run_job_inline")
    def test_no_worker_runs_inline(self, run_inline, _avail):
        from accounts.models import User
        from catalog.models import Scenario
        from ops.services import enqueue_scenario_job
        user = User.objects.create_user(email="c@d.co", password="pw12345!")
        Scenario.objects.create(scenario_id="scn", owner=user, definition={"steps": []})
        enqueue_scenario_job(user_id_str=str(user.id), scenario_id="scn", dry_run=True, current_user=user)
        run_inline.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test ops.tests.test_runner_bridge.DispatchTests -v 1`
Expected: FAIL — `cannot import name '_celery_worker_available'`.

- [ ] **Step 3: Implement dispatch in `ops/services.py`**

Add near the job orchestrators:

```python
def _celery_worker_available() -> bool:
    """Best-effort: True if at least one Celery worker answers a quick ping.

    RUN_JOBS_INLINE forces the decision: 'true' -> always inline (return
    False), 'false' -> never inline (return True), 'auto' (default) -> probe.
    """
    from django.conf import settings

    mode = str(getattr(settings, "RUN_JOBS_INLINE", "auto")).lower()
    if mode == "true":
        return False
    if mode == "false":
        return True
    try:
        from foxrunner.celery import celery_app

        replies = celery_app.control.inspect(timeout=0.5).ping()
        return bool(replies)
    except Exception:
        return False


def _run_job_inline(job_id: str, scenario_id: str, dry_run: bool) -> None:
    """Run the job in a background daemon thread (no Celery worker).

    Each thread gets its own DB connection; we close it on exit so we don't
    leak connections in the web process.
    """
    import threading

    from django.db import connection

    def _target() -> None:
        from ops.runner_bridge import execute_scenario_job

        try:
            execute_scenario_job(job_id, scenario_id, dry_run)
        except Exception:  # already recorded on the Job row by the bridge
            pass
        finally:
            connection.close()

    threading.Thread(target=_target, name=f"job-{job_id}", daemon=True).start()
```

Then change `enqueue_scenario_job` dispatch block (replace the `.delay` + event):

```python
    if _celery_worker_available():
        from ops.tasks import run_scenario_job

        task = run_scenario_job.delay(job.job_id, scenario_id, dry_run)
        set_celery_task_id(job.job_id, task.id)
        append_job_event(job_id=job.job_id, event_type="submitted",
                         message="Tâche Celery soumise.", payload={"celery_task_id": task.id})
    else:
        append_job_event(job_id=job.job_id, event_type="submitted",
                         message="Exécution inline (sans Celery).")
        _run_job_inline(job.job_id, scenario_id, dry_run)
    job.refresh_from_db()
    return serialize_job(job)
```

Apply the same dispatch swap in `retry_job_for_user`.

- [ ] **Step 4: Delegate the Celery task to the bridge (`ops/tasks.py`)**

Replace the body of `run_scenario_job` with:

```python
@celery_app.task(name="ops.tasks.run_scenario_job")
def run_scenario_job(job_id: str, scenario_id: str, dry_run: bool) -> dict:
    from ops.runner_bridge import execute_scenario_job

    return execute_scenario_job(job_id, scenario_id, dry_run)
```

- [ ] **Step 5: Add the setting + env example**

`foxrunner/settings.py` (near the MONITOR_* block):

```python
# Job execution transport: 'auto' (inline when no Celery worker), 'true'
# (force inline), 'false' (force Celery). Lets the live step view work on a
# Windows box with no Celery worker.
RUN_JOBS_INLINE = os.getenv("RUN_JOBS_INLINE", "auto").lower()
```

`.env.example` (near run_stack tunables):

```bash
# Job execution transport for the live step-by-step view: auto|true|false
RUN_JOBS_INLINE=auto
```

- [ ] **Step 6: Run tests**

Run: `python manage.py test ops.tests.test_runner_bridge ops.tests.test_jobs_api -v 1`
Expected: PASS (existing jobs tests still green; mocked dispatch test passes).
Run: `python scripts/check_env_example.py`
Expected: `env-example:ok`

- [ ] **Step 7: Commit**

```bash
git add ops/services.py ops/tasks.py foxrunner/settings.py .env.example ops/tests/test_runner_bridge.py
git commit -m "feat(jobs): auto inline/Celery dispatch so live runs work without a worker"
```

---

## Phase C — Artifacts route

### Task 6: Owner-scoped failure-artifact endpoint

**Files:**
- Modify: `ops/api.py`
- Test: `ops/tests/test_job_artifacts_api.py`

Serves the failure screenshot/page-source for a job to its owner. The file
name is `{execution_id}.png` / `.html`; the job's execution id is stored on
`Job.result["execution_id"]` (add it in the bridge) so the route resolves the
file under `config.runtime.artifacts_dir`.

- [ ] **Step 1: Add execution_id capture in the bridge**

In `ops/runner_bridge.execute_scenario_job`, the success/failure `result`
already exists; capture the run's execution id. `SchedulerService.run_scenario`
generates it internally; expose it by having `run_scenario` return it is
invasive — instead, the sink sees no id. Simplest: the bridge generates the id
and the frontend builds the artifact path from `{job_id}`. **Decision:** name
failure artifacts by **job_id** in the inline/job path. Pass an
`artifact_basename=job_id` down to `run_task` via `initial_context["execution_id"]`
through a new `run_scenario(..., execution_id=None)` param; the runner already
names artifacts `{execution_id}.png`. So set `execution_id=job_id` for job runs.
Add `execution_id` param to `SchedulerService.run_scenario` and `_run_once`
(default `None` → keep `uuid4().hex`).

- [ ] **Step 2: Write the failing test**

```python
# ops/tests/test_job_artifacts_api.py
from __future__ import annotations

from pathlib import Path

from django.test import Client, TestCase

from accounts.models import User
from app.config import load_config
from ops.models import Job


class JobArtifactsApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(email="o@x.co", password="pw12345!")
        self.other = User.objects.create_user(email="z@x.co", password="pw12345!")
        self.job = Job.objects.create(job_id="jA", user=self.owner, kind="run_scenario",
                                      target_id="scn", status="failed", dry_run=False)
        shots = load_config().runtime.artifacts_dir / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        (shots / "jA.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def _token(self, email):
        r = self.client.post("/api/v1/auth/jwt/login",
                             data=f"username={email}&password=pw12345!",
                             content_type="application/x-www-form-urlencoded")
        return r.json()["access_token"]

    def test_owner_gets_screenshot(self):
        r = self.client.get("/api/v1/jobs/jA/artifacts/screenshot",
                            HTTP_AUTHORIZATION=f"Bearer {self._token('o@x.co')}")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r["Content-Type"], "image/png")

    def test_other_user_forbidden(self):
        r = self.client.get("/api/v1/jobs/jA/artifacts/screenshot",
                            HTTP_AUTHORIZATION=f"Bearer {self._token('z@x.co')}")
        self.assertEqual(r.status_code, 403)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python manage.py test ops.tests.test_job_artifacts_api -v 1`
Expected: FAIL — 404 (route missing).

- [ ] **Step 4: Implement the route in `ops/api.py`**

```python
from django.http import FileResponse, HttpResponse  # add to imports

_ARTIFACT_KINDS = {"screenshot": ("screenshots", ".png", "image/png"),
                   "page_source": ("pages", ".html", "text/html; charset=utf-8")}


@router.get("/jobs/{job_id}/artifacts/{kind}", tags=["jobs"])
def get_job_artifact_endpoint(request, job_id: str, kind: str):
    from app.config import load_config

    if kind not in _ARTIFACT_KINDS:
        raise HttpError(404, "Type d'artefact inconnu.")
    record = ops_services.get_job_for_user(job_id, request.auth, is_superuser=request.auth.is_superuser)
    subdir, ext, content_type = _ARTIFACT_KINDS[kind]
    path = load_config().runtime.artifacts_dir / subdir / f"{record.job_id}{ext}"
    if not path.exists():
        raise HttpError(404, "Artefact introuvable.")
    return FileResponse(path.open("rb"), content_type=content_type)
```

> `get_job_for_user` already raises 403 for non-owners / 404 for missing.

- [ ] **Step 5: Run test to verify it passes**

Run: `python manage.py test ops.tests.test_job_artifacts_api -v 1`
Expected: PASS (2 tests).

- [ ] **Step 6: Regenerate OpenAPI + commit**

```bash
python scripts/export_openapi.py && python scripts/check_openapi.py
git add ops/api.py ops/runner_bridge.py scheduler/service.py ops/tests/test_job_artifacts_api.py openapi.json
git commit -m "feat(jobs): owner-scoped failure-artifact endpoint (screenshot/page_source)"
```

---

### Task 7: Backend gate sweep

- [ ] **Step 1: Run all backend gates**

```bash
ruff check . && ruff format --check .
python -m compileall accounts app catalog cli foxrunner network operations ops scenarios scheduler scripts state tests
coverage run --source=app,cli,network,operations,scenarios,scheduler,state -m unittest discover -s tests && coverage report --fail-under=75
DATABASE_URL=sqlite:///.runtime/django-cov.db coverage run --source=accounts,catalog,ops,foxrunner manage.py test catalog ops accounts foxrunner --parallel=1 && coverage report --fail-under=84
python scripts/check_openapi.py && python scripts/check_docs.py && python scripts/check_env_example.py
```

Expected: all pass. Fix any ruff-format with `ruff format <file>`; add missing coverage with focused tests; if `check_docs` flags the new route, add a line to `docs/API.md` or `docs/OBSERVABILITY.md`.

- [ ] **Step 2: Commit any fixups**

```bash
git add -A && git commit -m "chore(jobs): satisfy CI gates for live execution backend"
```

---

## Phase D — Frontend A21 (FoxRunner_frontend)

> Run all frontend commands from `C:\Users\Renaud\WebstormProjects\FoxRunner_frontend`.

### Task 8: French step-label helper

**Files:**
- Create: `src/app/core/api/step-label.ts`
- Test: `src/app/core/api/step-label.spec.ts`

- [ ] **Step 1: Write the failing test**

```ts
// step-label.spec.ts
import { describe, expect, it } from 'vitest';
import { stepLabel, stepId } from './step-label';

describe('stepLabel', () => {
  it('renders open_url in French with the url', () => {
    expect(stepLabel({ type: 'open_url', url: 'https://x' })).toBe('Ouvrir la page « https://x »');
  });
  it('renders click with locator', () => {
    expect(stepLabel({ type: 'click', locator: '#go' })).toBe('Cliquer sur « #go »');
  });
  it('falls back to the raw type when unknown', () => {
    expect(stepLabel({ type: 'weird_step' })).toBe('weird_step');
  });
});

describe('stepId', () => {
  it('builds collection[index]', () => {
    expect(stepId('steps', 2)).toBe('steps[2]');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ng test --watch=false` (or `npx vitest run src/app/core/api/step-label.spec.ts`)
Expected: FAIL — cannot find `./step-label`.

- [ ] **Step 3: Implement `src/app/core/api/step-label.ts`**

```ts
export type StepLike = { type: string; [k: string]: unknown };

export function stepId(collection: string, index: number): string {
  return `${collection}[${index}]`;
}

const q = (v: unknown): string => `« ${String(v ?? '')} »`;

const LABELS: Record<string, (s: StepLike) => string> = {
  open_url: (s) => `Ouvrir la page ${q(s['url'])}`,
  click: (s) => `Cliquer sur ${q(s['locator'])}`,
  input_text: (s) => `Saisir du texte dans ${q(s['locator'])}`,
  wait_for_element: (s) => `Attendre l'élément ${q(s['locator'])}`,
  assert_text: (s) => `Vérifier le texte de ${q(s['locator'])}`,
  assert_attribute: (s) => `Vérifier l'attribut de ${q(s['locator'])}`,
  select_option: (s) => `Choisir une option dans ${q(s['locator'])}`,
  extract_text_to_context: (s) => `Extraire le texte de ${q(s['locator'])}`,
  extract_attribute_to_context: (s) => `Extraire un attribut de ${q(s['locator'])}`,
  screenshot: () => 'Capture d’écran',
  wait_until_url_contains: (s) => `Attendre que l'URL contienne ${q(s['value'])}`,
  wait_until_title_contains: (s) => `Attendre que le titre contienne ${q(s['value'])}`,
  close_browser: () => 'Fermer le navigateur',
  sleep: (s) => `Attendre ${String(s['seconds'] ?? '?')} s`,
  sleep_random: () => 'Attendre une durée aléatoire',
  notify: () => 'Envoyer une notification',
  http_request: (s) => `Requête HTTP vers ${q(s['url'])}`,
  require_enterprise_network: () => 'Exiger le réseau d’entreprise',
  set_context: (s) => `Définir la variable ${q(s['key'])}`,
  format_context: (s) => `Composer la variable ${q(s['key'])}`,
  group: () => 'Groupe d’étapes',
  parallel: () => 'Étapes en parallèle',
  repeat: (s) => `Répéter ${String(s['times'] ?? '?')} fois`,
  try: () => 'Bloc try / catch',
};

export function stepLabel(step: StepLike): string {
  const fn = LABELS[step.type];
  return fn ? fn(step) : step.type;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/app/core/api/step-label.spec.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/app/core/api/step-label.ts src/app/core/api/step-label.spec.ts
git commit -m "feat(exec): French step-label helper + step-id builder"
```

---

### Task 9: Regenerate schema + types + service artifact URL

**Files:**
- Modify: `src/app/core/api/schema.ts` (regen), `src/app/core/api/types.ts`, `src/app/core/api/jobs.service.ts`

- [ ] **Step 1: Regenerate schema from the backend openapi**

With the backend `openapi.json` up to date (Task 6), copy + generate:

```bash
cp /d/PycharmProjects/FoxRunner_server/openapi.json ./openapi.local.json
npm run gen:api:file
rm -f openapi.local.json
```

Expected: `schema.ts` rewritten; `git status` shows only `schema.ts`.

- [ ] **Step 2: Add the artifact URL helper to `jobs.service.ts`**

```ts
  artifactUrl(jobId: string, kind: 'screenshot' | 'page_source'): string {
    return `${this.base}/jobs/${encodeURIComponent(jobId)}/artifacts/${kind}`;
  }
```

> Note: the `<img>` needs the JWT. Since the app keeps JWT in memory (no
> cookie), fetch the image via `HttpClient` (interceptor adds the bearer) and
> bind a blob URL. Add:

```ts
  async artifactBlob(jobId: string, kind: 'screenshot' | 'page_source'): Promise<string> {
    const blob = await firstValueFrom(
      this.http.get(this.artifactUrl(jobId, kind), { responseType: 'blob' }),
    );
    return URL.createObjectURL(blob);
  }
```

- [ ] **Step 3: Build to typecheck**

Run: `npm run build`
Expected: success (size warning OK).

- [ ] **Step 4: Commit**

```bash
git add src/app/core/api/schema.ts src/app/core/api/jobs.service.ts src/app/core/api/types.ts
git commit -m "chore(exec): regen schema + job artifact URL/blob helpers"
```

---

### Task 10: Execution view (rewrite job-detail)

**Files:**
- Modify: `src/app/features/jobs/detail/job-detail.component.ts`

Build the checklist by parsing the scenario `definition` (fetched via
`ScenariosService.get(userId, job.target_id)`) into ordered
`{collection, index, step_id, label}` rows, then overlay status from events
keyed by `event.step`. Status precedence per `step_id`: failed > succeeded >
skipped > running(started, no terminal yet) > pending.

- [ ] **Step 1: Add the component logic (status mapping)**

Add fields + a `computed` checklist. Inject `ScenariosService`. On load, fetch
job + events + scenario definition. Map:

```ts
type StepRow = { collection: string; stepId: string; label: string; type: string };
type StepStatus = 'pending' | 'running' | 'ok' | 'failed' | 'skipped';

private statusFor(stepId: string): StepStatus {
  const evs = this.events().filter((e) => e.step === stepId);
  if (evs.some((e) => e.event_type === 'step_failed')) return 'failed';
  if (evs.some((e) => e.event_type === 'step_succeeded')) return 'ok';
  if (evs.some((e) => e.event_type === 'step_skipped')) return 'skipped';
  if (evs.some((e) => e.event_type === 'step_started')) return 'running';
  return 'pending';
}
```

Build rows from the definition's five collections in order
(`before_steps`, `steps`, `on_success`, `on_failure`, `finally_steps`) using
`stepId(collection, i)` + `stepLabel(step)`. Compute progress
`done = rows where status in {ok,failed,skipped}`, `total = rows.length`.
Elapsed time from `job.started_at` → `finished_at ?? now`.

- [ ] **Step 2: Add the template (header + checklist + failure card)**

Header: scenario id, dry-run/réel tag, `app-status-tag`, `n/N étapes`,
`p-progressbar`, elapsed. Checklist grouped by collection (PrimeNG: a simple
`@for` over rows, group separators; before/finally/on_* in a collapsed
`p-panel` toggled — or just rendered with a muted heading). Each row: status
icon (`markerIcon`/`markerClass` reused) + `label` + duration. Failure card:
when `job.status === 'failed'`, show the failing row label + its event message,
the screenshot via `artifactBlob`, and `p-panel`-collapsed traceback (from the
failed event payload `traceback`) and a "HTML de la page" link. Buttons:
"Relancer" / "Relancer en dry-run" → `service.trigger(userId, job.target_id, dryRun)`
then navigate to the new job.

Keep the existing raw timeline under a collapsible "Journal détaillé".
Lower the auto-refresh interval to 1500ms while `queued|running`.

> Full template omitted here for brevity — follow the existing component's
> PrimeNG idioms (it already imports Card, Tag, Timeline, Button). Add
> `ProgressBarModule` and `PanelModule` to imports.

- [ ] **Step 3: Lint + build + run vitest**

```bash
npm run lint && npm run build && ng test --watch=false
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/app/features/jobs/detail/job-detail.component.ts
git commit -m "feat(exec): live step checklist + failure card with inline screenshot"
```

---

### Task 11: Launch buttons on scenario detail

**Files:**
- Modify: `src/app/features/scenarios/detail/` component

- [ ] **Step 1: Add "Lancer (dry-run)" / "Lancer (réel)" buttons**

In the scenario detail header, add two `p-button`s calling a method that does
`const job = await jobsService.trigger(me.id, scenarioId, dryRun); router.navigate(['/jobs', job.job_id])`.
Inject `JobsService`, `AuthService`, `Router`. Confirm "réel" with a
`p-confirmdialog` or a simple guard.

- [ ] **Step 2: Lint + build**

```bash
npm run lint && npm run build
```
Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add src/app/features/scenarios/detail
git commit -m "feat(exec): launch scenario (dry-run/réel) from detail → live view"
```

---

## Phase E — Frontend node20 (FoxRunner_frontend_node20)

### Task 12: Mirror to Angular 19 / PrimeNG 19

**Files:** same paths under `C:\Users\Renaud\WebstormProjects\FoxRunner_frontend_node20`.

Apply Tasks 8–11 identically, with these deltas (per the repo's CLAUDE.md):
- Vitest run command is `npm test` (not `ng test`).
- PrimeNG templates use `pTemplate="..."` (e.g. `<ng-template pTemplate="content">`),
  not Angular-21 `#name` refs. `p-card` custom header → plain content block.
- `step-label.ts` / `step-label.spec.ts` are framework-agnostic — copy verbatim.
- `jobs.service.ts`, `types.ts` additions — copy verbatim.
- Regenerate `schema.ts` the same way (`gen:api:file` with the backend openapi).

- [ ] **Step 1: Copy `step-label.ts` + spec verbatim; run `npm test` on the spec**

Run: `npx vitest run src/app/core/api/step-label.spec.ts`
Expected: PASS.

- [ ] **Step 2: Regenerate schema + add service/types helpers (verbatim from Task 9)**

```bash
cp /d/PycharmProjects/FoxRunner_server/openapi.json ./openapi.local.json
npm run gen:api:file
rm -f openapi.local.json
```

- [ ] **Step 3: Port the execution view + launch buttons (Tasks 10–11) with pTemplate syntax**

- [ ] **Step 4: Lint + build + test**

```bash
npm run lint && npm run build && npm test
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/app/core/api/step-label.ts src/app/core/api/step-label.spec.ts src/app/core/api/schema.ts src/app/core/api/jobs.service.ts src/app/core/api/types.ts src/app/features/jobs/detail src/app/features/scenarios/detail
git commit -m "feat(exec): live execution view (node20 / Angular 19 port)"
```

---

## Phase F — Manual verification

### Task 13: End-to-end smoke (local, inline mode)

- [ ] **Step 1: Start backend + frontend**

Backend: `RUN_JOBS_INLINE=auto` (default). `python scripts/run_stack.py` (or
`manage.py runserver`). Frontend A21: `npm start`.

- [ ] **Step 2: Drive it**

Log in → open a scenario → "Lancer (dry-run)" → confirm the live view shows
steps checking off (poll ~1.5s) and a green success header. Then craft a
failing step (e.g. `wait_for_element` on a missing locator, non-dry) → confirm
the failure card shows the failing step in French + the screenshot thumbnail +
expandable traceback.

- [ ] **Step 3: Note any gaps** for a follow-up task; do not silently skip.

---

## Self-Review Notes

- **Spec coverage:** sink + traceback (T1–T3), step_id (T2), auto inline/Celery (T5), artifacts route (T6), checklist+header+failure card+relaunch (T10–T11), FR labels (T8), both frontends (T8–T12), gates (T7, per-task). ✔
- **Deferred (documented):** `step_retrying` events and nested-block granularity are out of v1 scope (spec §Périmètre). Frontend full templates are described by structure + idioms rather than pasted verbatim (two near-identical large components) — the engineer follows the existing component's PrimeNG patterns.
- **Type consistency:** `step_id` format `collection[index]` is identical in `runner.py` (`f"steps[{i}]"`), the bridge (passes `ev.step_id` to `JobEvent.step`), and the frontend (`stepId()` + `statusFor`). Event types match across engine, bridge sink, and `statusFor`.
