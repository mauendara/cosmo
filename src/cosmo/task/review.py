"""The `REVIEWING` verdict file contract (v4 workflow changes, see
`docs/v4-changes-to-workflow-plan.md`).

`HarnessAdapter.review()` returns a uniform `HarnessResult` like every other
adapter method (spec 2.2), but a review's actual verdict -- approved or
rejected, and why -- has no harness-agnostic slot on that dataclass (see
`HarnessAdapter.review`'s own docstring for why it isn't one: spec 4
prohibits treating the session's free-text output as a signal). Instead the
reviewer writes a small structured file to the worktree, at a fixed path,
and this module reads it back -- the same "watch a file the harness writes"
shape `task.progress.read_progress_from_file` already uses for `tasks.md`,
just a fixed single-shot file instead of a polled one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REVIEW_RESULT_RELATIVE_PATH = Path(".cosmo") / "review-result.json"
"""Relative to the task's worktree root. Never committed -- written after
the implementer's own commit (`REVIEWING` runs after `VALIDATING`, before
`COMMITTING`'s scoped `git add docs/decisions-log.md`), so it never enters
the task's git history; it is simply discarded with the rest of the worktree
once the task reaches a terminal state."""


@dataclass(frozen=True, slots=True)
class ReviewVerdict:
    approved: bool
    reason: str | None


def review_result_path(worktree_path: Path) -> Path:
    return worktree_path / REVIEW_RESULT_RELATIVE_PATH


def read_review_verdict(worktree_path: Path) -> ReviewVerdict | None:
    """`None` covers every "no real verdict" case uniformly: the file is
    missing, unreadable, not valid JSON, or missing/malformed its required
    `verdict` key -- `task.machine._do_reviewing` treats all of these as an
    environment problem with the review call itself (the same posture
    `task.classify` already takes for a `propose`/`implement` call that
    didn't produce a usable result), never as a rejection."""
    path = review_result_path(worktree_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    verdict = data.get("verdict")
    if verdict == "approved":
        return ReviewVerdict(approved=True, reason=None)
    if verdict == "rejected":
        reason = data.get("reason")
        return ReviewVerdict(approved=False, reason=reason if isinstance(reason, str) else None)
    return None
