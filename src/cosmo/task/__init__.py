"""The spec 3.2 task state machine (Phase 7): `task.machine.run_task` drives
one task through every state, with per-state timeouts (`task.timeouts`),
progress/liveness watching (`task.progress`), failure classification
(`task.classify`), and informed retries (`task.retry`).
"""

from __future__ import annotations

from cosmo.task.machine import run_task
from cosmo.task.types import TaskContext

__all__ = ["run_task", "TaskContext"]
