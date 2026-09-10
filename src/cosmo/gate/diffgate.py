"""Spec 6.1 layer 2: the diff gate.

Detection, not prevention -- layer 1 (`PreToolUse` hooks, spec 2.5) is the
strongest defense and lives in `templates/harness/claude/hooks/`; this is
what still catches an adapter with `supports_gating: false`, or a hook that
was bypassed. Runs *before* tests execute (spec 6.1), against
`git diff <base_branch>...<task_branch>` from the task's own worktree --
this is a fresh, standalone git invocation, deliberately not reusing
`cosmo.git.merge.attempt_merge_ladder`, which runs the repo-level
merge/rebase, not a diff computation (see Phase 5 handoff notes).

Assertion counting (Open Item 1) is a line-count heuristic per test
framework, not a real parser: counting `assertThat(`/`assert`/`expect(`
call sites on added vs. removed lines. This will under/over-count a
multi-line assertion or a helper method that wraps an assertion, but it
fails safe -- a heuristic that only ever *under*-counts removals (never
mistakes an unrelated line for a removed assertion) means the worst case is
a real violation slipping through occasionally, not a false failure blocking
honest work. The spec explicitly defers a real per-language parser to a
follow-up spec.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from cosmo.config.model import GateConfig
from cosmo.gate.structural_checks import count_assertions, is_test_path
from cosmo.gate.types import DiffGateResult, DiffGateViolation


@dataclass(frozen=True, slots=True)
class DiffFile:
    path: str
    status: str  # git's name-status letter: A, M, D, R100, ...
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)

    @property
    def is_deleted(self) -> bool:
        return self.status.startswith("D")

    @property
    def is_added(self) -> bool:
        return self.status.startswith("A")

    @property
    def net_loc_change(self) -> int:
        return len(self.added_lines) - len(self.removed_lines)


def compute_diff(worktree_path: Path, base_branch: str, task_branch: str) -> list[DiffFile]:
    name_status = subprocess.run(
        ["git", "diff", "--name-status", f"{base_branch}...{task_branch}"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    statuses: dict[str, str] = {}
    for line in name_status.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status, path = parts[0], parts[-1]
        statuses[path] = status

    patch = subprocess.run(
        ["git", "diff", "--unified=0", "--no-color", f"{base_branch}...{task_branch}"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    files: dict[str, DiffFile] = {}
    current: str | None = None
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            match = re.match(r"diff --git a/(.+) b/(.+)", line)
            new_path: str | None = match.group(2) if match else None
            if new_path is not None:
                current = new_path
                files[current] = DiffFile(path=current, status=statuses.get(current, "M"))
            continue
        if current is None:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            files[current].added_lines.append(line[1:])
        elif line.startswith("-"):
            files[current].removed_lines.append(line[1:])

    return list(files.values())


def run_diff_gate(
    *,
    worktree_path: Path,
    base_branch: str,
    task_branch: str,
    gate: GateConfig,
    allow_test_edits: bool,
) -> DiffGateResult:
    """Spec 6.1 layer 2. `allow_test_edits` (G4, `docs/v15-fixes-after-wa-
    chat-run.md`) only bypasses the blanket "modified or deleted" rule
    below -- the assertion-count/skip-annotation/LOC-drop checks always
    run regardless, so a task granted this escape hatch still can't
    silently weaken assertions or introduce a skip annotation. Before this
    fix, `allow_test_edits=True` skipped this whole function outright,
    which was strictly *more* permissive than the escape hatch needed to
    be."""
    diff_files = compute_diff(worktree_path, base_branch, task_branch)
    test_files = [
        f for f in diff_files if is_test_path(f.path, gate.diff_gate_test_path_patterns) is not None
    ]

    violations: list[DiffGateViolation] = []

    if not allow_test_edits:
        # Spec 6.1 layer 2's own wording is "modified or deleted" -- a newly
        # *added* test file is exactly what a well-behaved agent is expected
        # to produce for new work, and is deliberately not flagged here
        # (confirmed against a real scenario by hand: an early version of
        # this gate rejected every task that added a new e2e test at all,
        # which defeats the point of an autonomous agent that writes its own
        # tests). A new file is still subject to the assertion-count/skip-
        # annotation/LOC checks below -- an added-but-immediately-disabled
        # test is still suspicious.
        for f in test_files:
            if f.is_added:
                continue
            if f.is_deleted:
                violations.append(
                    DiffGateViolation(
                        kind="test_path_deleted",
                        detail=f"test file deleted: {f.path}",
                        file=f.path,
                    )
                )
            else:
                violations.append(
                    DiffGateViolation(
                        kind="test_path_modified",
                        detail=f"test file modified: {f.path}",
                        file=f.path,
                    )
                )

    # Excludes a deleted file the same way the LOC-drop check below already
    # does: removing a file removes all of its assertions "by design," in
    # plain sight in the diff -- not the sneaky case this check exists for,
    # which is a file that *survives* but has had assertions quietly
    # weakened inside it. `allow_test_edits`'s own "legitimately deleting an
    # obsolete test" escape hatch (this fix's own design note) would
    # otherwise be unusable for any test with real assertions in it.
    net_assertions = sum(
        count_assertions(f.added_lines) - count_assertions(f.removed_lines)
        for f in test_files
        if not f.is_deleted
    )
    if net_assertions < 0:
        violations.append(
            DiffGateViolation(
                kind="assertion_count_decreased",
                detail=f"net assertion count decreased by {-net_assertions} across the diff",
                file=None,
            )
        )

    for f in test_files:
        for line in f.added_lines:
            hit = next((a for a in gate.diff_gate_skip_annotations if a in line), None)
            if hit is not None:
                violations.append(
                    DiffGateViolation(
                        kind="skip_annotation_introduced",
                        detail=f"introduced {hit!r} in {f.path}",
                        file=f.path,
                    )
                )

    for f in test_files:
        if not f.is_deleted and -f.net_loc_change > gate.diff_gate_loc_drop_threshold:
            violations.append(
                DiffGateViolation(
                    kind="test_loc_dropped",
                    detail=(
                        f"{f.path} lost {-f.net_loc_change} lines, "
                        f"exceeding the {gate.diff_gate_loc_drop_threshold}-line threshold"
                    ),
                    file=f.path,
                )
            )

    return DiffGateResult(passed=not violations, violations=violations)
