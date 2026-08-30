"""`decisions-log.md`: spec 11's structured, queryable audit entry, appended
by Cosmo itself rather than the harness so its format (decision + date +
task id) never drifts -- an LLM asked to keep re-formatting this exactly
right across hundreds of tasks is exactly the kind of unverified self-report
spec 11 says this loop should not depend on.

Every task that reaches `COMMITTING` gets exactly one line here,
unconditionally -- trading spec 11's conditional "if a completed task
introduced a decision" for a cheap, always-consistent entry. Deciding
"was this actually a decision" would require the same kind of LLM
self-judgment spec 11 is designed to avoid; see
`docs/v3-implementation-state.md`'s Phase 7 section.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

_HEADER = "# Decisions Log\n\nStructured entries: date | task | spec, appended by Cosmo.\n\n"


def append_decision_entry(
    worktree_path: Path,
    *,
    task_id: str,
    spec_path: str,
    when: str | None = None,
) -> Path:
    log_path = worktree_path / "docs" / "decisions-log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    date = when or datetime.now(UTC).date().isoformat()

    if not log_path.is_file():
        log_path.write_text(_HEADER, encoding="utf-8")

    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"- {date} | {task_id} | {spec_path}\n")

    return log_path
