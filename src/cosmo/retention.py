"""Spec 9.5 log retention: `raw_log_path` files rotate per task, kept for a
window keyed off the task's current terminal status -- 7 days for `DONE`,
30 for `BLOCKED` (`config.log_retention`, defaults.toml). Nothing else in
the codebase rotates or deletes anything under `paths.log_dir`.

Playwright trace/screenshot retention (spec 9.5's other bullet, "retained
only for failing runs") needs no code here: `gate.parsers.parse_playwright_
json` only ever appends to `StageResult.artifact_paths` from a *failed*
test's own attachments (see its own source), so a task that reaches `DONE`
has an empty `artifact_paths` by construction -- there is nothing to prune.
Those artifacts also live inside the task's worktree, not `paths.log_dir`,
so they are out of scope for this module regardless; worktree lifecycle
(spec 3.2) is `git.worktree`'s territory, not this one's.

Deliberately keyed by the task's *current* status looked up from the store,
not by a status recorded at the time each log file was written -- a task
retried after `BLOCKED` and later reaching `DONE` should have its older
attempt's logs age out on the shorter `DONE` window, matching spec 9.5's own
framing ("retained 7 days for DONE") as a property of the task's outcome,
not of any one attempt.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from cosmo.config.model import CosmoConfig, LogRetentionConfig
from cosmo.store.reader import get_task

_SECONDS_PER_DAY = 86400


@dataclass(frozen=True, slots=True)
class RetentionSummary:
    files_removed: int
    bytes_removed: int


def apply_log_retention(config: CosmoConfig, *, now: float | None = None) -> RetentionSummary:
    """Walks `paths.log_dir/harness/<task_id>/*.ndjson` (the only thing
    `harness.claude.adapter._invoke` ever writes there) and deletes any log
    file older than its task's retention window. A task_id directory with
    no matching `task_queue` row, or one that hasn't reached a terminal
    status yet, is left alone entirely -- this function only ever deletes
    logs it can positively attribute to a finished task."""
    harness_log_dir = config.paths.log_dir / "harness"
    if not harness_log_dir.is_dir():
        return RetentionSummary(files_removed=0, bytes_removed=0)

    reference = now if now is not None else time.time()
    files_removed = 0
    bytes_removed = 0

    for task_dir in sorted(p for p in harness_log_dir.iterdir() if p.is_dir()):
        task = get_task(config.paths.db_path, task_dir.name)
        if task is None:
            continue
        threshold_days = _threshold_days(task.status, config.log_retention)
        if threshold_days is None:
            continue
        cutoff = reference - threshold_days * _SECONDS_PER_DAY

        for log_file in list(task_dir.iterdir()):
            if not log_file.is_file():
                continue
            stat = log_file.stat()
            if stat.st_mtime < cutoff:
                size = stat.st_size
                log_file.unlink()
                files_removed += 1
                bytes_removed += size

        if not any(task_dir.iterdir()):
            task_dir.rmdir()

    return RetentionSummary(files_removed=files_removed, bytes_removed=bytes_removed)


def _threshold_days(status: str, config: LogRetentionConfig) -> int | None:
    if status == "done":
        return config.done_days
    if status == "blocked":
        return config.blocked_days
    return None
