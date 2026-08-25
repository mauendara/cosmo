"""Informed retries (spec 6.3): the retry prompt carries the previous
attempt's `error_detail` plus a short summary of what was already tried, so
the harness doesn't repeat a failed approach. Reads back what
`record_task_failure` already persisted (`store.reader.list_task_failures`)
rather than threading `GateResult`/failure detail across the retry boundary
by hand -- matches spec 11's "deterministic and queryable" posture.
"""

from __future__ import annotations

from cosmo.store.reader import TaskFailureRow


def build_retry_context(failures: list[TaskFailureRow]) -> str | None:
    """`failures` should already be filtered to the current task and, where
    relevant, the current retry cycle. Returns `None` when there is nothing
    to say yet (first attempt) -- `HarnessAdapter.implement`'s own
    `retry_context: str | None = None` default matches this exactly."""
    if not failures:
        return None

    latest = failures[-1]
    lines = [
        f"Attempt {latest.attempt_number} failed at stage {latest.failure_stage} "
        f"({latest.failure_type}): {latest.error_summary}"
    ]
    if latest.error_detail:
        lines.append(latest.error_detail)

    if len(failures) > 1:
        lines.append("")
        lines.append("Previous attempts:")
        for f in failures[:-1]:
            lines.append(f"- attempt {f.attempt_number} ({f.failure_stage}): {f.error_summary}")

    return "\n".join(lines)
