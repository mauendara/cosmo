"""Unit tests for `task.review`'s verdict-file contract, focused on the
stray-file fallback in `read_review_verdict` (found by hand: a real
`wa-chat-text-bubbles` review approved its diff but wrote the verdict to
`frontend/.cosmo/review-result.json` after `cd frontend && npm run build`
shifted the session's cwd, and the canonical worktree-root-only read
silently discarded that approval as "no usable verdict")."""

from __future__ import annotations

import json
from pathlib import Path

from cosmo.task.review import read_review_verdict, review_result_path


def test_verdict_at_canonical_path_is_read_directly(tmp_path: Path) -> None:
    path = review_result_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"verdict": "approved"}), encoding="utf-8")

    verdict = read_review_verdict(tmp_path)

    assert verdict is not None
    assert verdict.approved is True


def test_verdict_written_under_a_subdirectory_is_still_found(tmp_path: Path) -> None:
    stray = tmp_path / "frontend" / ".cosmo" / "review-result.json"
    stray.parent.mkdir(parents=True)
    stray.write_text(
        json.dumps({"verdict": "rejected", "reason": "missing edge case"}), encoding="utf-8"
    )

    verdict = read_review_verdict(tmp_path)

    assert verdict is not None
    assert verdict.approved is False
    assert verdict.reason == "missing edge case"


def test_fallback_search_skips_pruned_directories(tmp_path: Path) -> None:
    stray = tmp_path / "node_modules" / "some-pkg" / ".cosmo" / "review-result.json"
    stray.parent.mkdir(parents=True)
    stray.write_text(json.dumps({"verdict": "approved"}), encoding="utf-8")

    assert read_review_verdict(tmp_path) is None


def test_no_verdict_file_anywhere_returns_none(tmp_path: Path) -> None:
    assert read_review_verdict(tmp_path) is None
