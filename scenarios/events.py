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
