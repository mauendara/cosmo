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
import os
from dataclasses import dataclass
from pathlib import Path

REVIEW_RESULT_RELATIVE_PATH = Path(".cosmo") / "review-result.json"
"""Relative to the task's worktree root. Never committed -- written after
the implementer's own commit (`REVIEWING` runs after `VALIDATING`, before
`COMMITTING`'s scoped `git add docs/decisions-log.md`), so it never enters
the task's git history; it is simply discarded with the rest of the worktree
once the task reaches a terminal state."""

_SEARCH_PRUNE_DIRS = frozenset({"node_modules", ".git", "dist", "build", "target"})
"""Directories `_find_stray_verdict_file` never descends into -- large,
never contain a reviewer's own working directory, and would make the
fallback scan slow on a real frontend checkout."""


@dataclass(frozen=True, slots=True)
class ReviewVerdict:
    approved: bool
    reason: str | None


def review_result_path(worktree_path: Path) -> Path:
    return worktree_path / REVIEW_RESULT_RELATIVE_PATH


def _find_stray_verdict_file(worktree_path: Path) -> Path | None:
    """A review session's shell cwd can drift into a project subdirectory
    (e.g. this task's own real-world repro: `cd frontend && npm run build`
    inside a `vite-react-local` checkout) before it writes the verdict file
    with the relative path the reviewer prompt gives it -- landing at
    `<subdir>/.cosmo/review-result.json` instead of the worktree root's
    `.cosmo/review-result.json`. A real review that reads "approved" then
    silently becomes `read_review_verdict`'s `None` case, and
    `task.machine._do_reviewing` throws away an already-approved diff for a
    full, costly re-`IMPLEMENTING` retry that never needed to happen.

    This is a bounded, one-level-deep-per-branch structural search for any
    `.cosmo/review-result.json` under the worktree, skipping the
    directories in `_SEARCH_PRUNE_DIRS` -- not prose-parsing (spec 4 still
    holds: this only ever looks for the same fixed filename the canonical
    path already names), just not assuming *which* directory it landed in.
    Returns the most recently modified match if more than one stray file
    somehow exists; `None` if the search finds nothing."""
    candidates: list[Path] = []
    for root, dirnames, filenames in os.walk(worktree_path):
        dirnames[:] = [d for d in dirnames if d not in _SEARCH_PRUNE_DIRS]
        if Path(root).name == ".cosmo" and "review-result.json" in filenames:
            candidates.append(Path(root) / "review-result.json")
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def read_review_verdict(worktree_path: Path) -> ReviewVerdict | None:
    """`None` covers every "no real verdict" case uniformly: the file is
    missing, unreadable, not valid JSON, or missing/malformed its required
    `verdict` key -- `task.machine._do_reviewing` treats all of these as an
    environment problem with the review call itself (the same posture
    `task.classify` already takes for a `propose`/`implement` call that
    didn't produce a usable result), never as a rejection."""
    path = review_result_path(worktree_path)
    if not path.is_file():
        stray = _find_stray_verdict_file(worktree_path)
        if stray is None:
            return None
        path = stray
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
