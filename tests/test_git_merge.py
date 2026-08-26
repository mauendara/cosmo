"""The spec 3.4 merge-conflict ladder against real `git` -- the plan Phase 5
exit criterion: "A scripted two-task conflict scenario in a fixture repo:
rebase recovery succeeds in one case, and in the other produces BLOCKED with
merge_conflict, retained worktree, and a warning-severity task.blocked."

Case 1 (recovery succeeds) needs a scenario where a direct `git merge`
conflicts but `git rebase` onto the same target does not. The two are not
generally interchangeable (found by hand, empirically, before writing this
test): if a task branch's *net* diff genuinely disagrees with develop's
content, both merge and rebase hit the identical conflict, since rebase's
default merge-backend uses the same 3-way logic per commit. The reliable,
well-documented case where they diverge is patch-id "already applied"
detection: if the task branch's first commit is byte-identical (same diff)
to a commit already merged into develop, `git rebase` silently skips that
commit as empty and replays only what's left, while a flat `git merge` of
the whole (un-rebased) branch still sees a real disagreement against
develop's current tip and conflicts. Verified against real git before this
test was written; see that experiment's outcome reproduced here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cosmo.events import EventEmitter
from cosmo.git.merge import MergeCommandError, attempt_merge_ladder, merge_task
from cosmo.git.worktree import create_worktree
from cosmo.store import StoreWriter
from cosmo.store.enums import BlockedReason
from cosmo.store.reader import get_task, list_events

AUTHOR = ("Cosmo Test", "cosmo-test@example.com")


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            f"user.name={AUTHOR[0]}",
            "-c",
            f"user.email={AUTHOR[1]}",
            *args,
        ],
        capture_output=True,
        text=True,
        check=check,
    )


def _repo_on_develop(tmp_path: Path) -> Path:
    repo = tmp_path / "target-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "f.txt").write_text("1\n2\n3\n4\n5\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "branch", "-M", "develop")
    return repo


def test_recovery_succeeds_when_rebase_skips_an_already_applied_commit(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    work_dir = tmp_path / "work"
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="task1", spec_path="p1", max_attempts=2)
    writer.queue_add(task_id="task2", spec_path="p2", max_attempts=2)
    emitter = EventEmitter(writer)

    # task1: line3 -> THREE, merged into develop directly (no ladder needed).
    task1 = create_worktree(
        repo_path=repo,
        work_dir=work_dir,
        run_id="run-1",
        task_id="task1",
        spec_id="task1",
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )
    (task1.path / "f.txt").write_text("1\n2\nTHREE\n4\n5\n")
    _git(task1.path, "add", "f.txt")
    _git(task1.path, "commit", "-q", "-m", "task1: line3->THREE")
    _git(repo, "merge", "-q", "--no-edit", task1.branch)

    # task2: branched before task1 merged. First commit makes the *same*
    # edit as task1 (identical diff -> matching patch-id); second commit
    # changes it further. Its direct merge into develop will conflict; its
    # rebase onto develop will silently skip the now-duplicate first commit.
    task2 = create_worktree(
        repo_path=repo,
        work_dir=work_dir,
        run_id="run-1",
        task_id="task2",
        spec_id="task2",
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )
    # task2's worktree was branched from develop *before* task1 merged, but
    # `create_worktree` always branches from the *current* base_branch tip --
    # rewind it to the pre-task1 commit to reproduce the scenario.
    pre_task1 = _git(repo, "rev-list", "--max-parents=0", "develop").stdout.strip()
    _git(task2.path, "reset", "--hard", pre_task1)
    (task2.path / "f.txt").write_text("1\n2\nTHREE\n4\n5\n")
    _git(task2.path, "add", "f.txt")
    _git(task2.path, "commit", "-q", "-m", "task2 commit1: line3->THREE (same as task1)")
    (task2.path / "f.txt").write_text("1\n2\nFOUR\n4\n5\n")
    _git(task2.path, "add", "f.txt")
    _git(task2.path, "commit", "-q", "-m", "task2 commit2: line3->FOUR")

    gate_calls = []

    def gate_rerun() -> bool:
        gate_calls.append(1)
        return True

    outcome = attempt_merge_ladder(
        repo_path=repo,
        worktree_path=task2.path,
        branch=task2.branch,
        base_branch="develop",
        gate_rerun=gate_rerun,
        author=AUTHOR,
    )

    assert outcome.merged is True
    assert outcome.rebase_attempted is True
    assert gate_calls == [1]
    assert (repo / "f.txt").read_text() == "1\n2\nFOUR\n4\n5\n"
    writer.close()


def test_blocked_with_merge_conflict_when_rebase_itself_conflicts(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    work_dir = tmp_path / "work"
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="task1", spec_path="p1", max_attempts=2)
    writer.queue_add(task_id="task2", spec_path="p2", max_attempts=2)
    emitter = EventEmitter(writer)
    # Phase 8: task_transitions.run_id/task_failures.run_id now carry a
    # real, FK-enforced value -- a run_state row must exist for "run-1"
    # before merge_task (a real writer of both) can reference it.
    writer.run_create(
        run_id="run-1",
        harness="claude",
        permission_mode="dontAsk",
        max_turns=80,
        base_branch="develop",
    )

    task1 = create_worktree(
        repo_path=repo,
        work_dir=work_dir,
        run_id="run-1",
        task_id="task1",
        spec_id="task1",
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )
    (task1.path / "f.txt").write_text("1\n2\nTHREE\n4\n5\n")
    _git(task1.path, "add", "f.txt")
    _git(task1.path, "commit", "-q", "-m", "task1: line3->THREE")
    _git(repo, "merge", "-q", "--no-edit", task1.branch)

    task2 = create_worktree(
        repo_path=repo,
        work_dir=work_dir,
        run_id="run-1",
        task_id="task2",
        spec_id="task2",
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )
    pre_task1 = _git(repo, "rev-list", "--max-parents=0", "develop").stdout.strip()
    _git(task2.path, "reset", "--hard", pre_task1)
    (task2.path / "f.txt").write_text("1\n2\nDIFFERENT\n4\n5\n")
    _git(task2.path, "add", "f.txt")
    _git(task2.path, "commit", "-q", "-m", "task2: line3->DIFFERENT (genuinely conflicts)")

    def gate_rerun() -> bool:
        raise AssertionError("the gate must never run when the rebase itself conflicts")

    result = merge_task(
        repo_path=repo,
        worktree_path=task2.path,
        branch=task2.branch,
        base_branch="develop",
        task_id="task2",
        run_id="run-1",
        writer=writer,
        emitter=emitter,
        gate_rerun=gate_rerun,
        author=AUTHOR,
    )

    assert result.outcome.merged is False
    assert result.outcome.blocked_reason is BlockedReason.MERGE_CONFLICT
    assert result.worktree_removed is False
    assert task2.path.is_dir(), "a blocked task's worktree must be retained for inspection"

    task_row = get_task(db_path, "task2")
    assert task_row is not None
    assert task_row.status == "blocked"
    assert task_row.blocked_reason == "merge_conflict"
    assert task_row.worktree_path == str(task2.path), "retained, not cleared"

    events = list_events(db_path, task_id="task2")
    blocked_events = [e for e in events if e.event_type == "task.blocked"]
    assert len(blocked_events) == 1
    assert blocked_events[0].severity == "warning"
    assert blocked_events[0].payload["blocked_reason"] == "merge_conflict"

    # repo_path (develop) must be left clean -- the aborted attempt must not
    # leave conflict markers or a half-finished merge behind.
    status = _git(repo, "status", "--porcelain")
    assert status.stdout.strip() == ""
    writer.close()


def test_a_clean_merge_needs_no_rebase(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    work_dir = tmp_path / "work"
    db_path = tmp_path / "cosmo.db"
    writer = StoreWriter(db_path)
    writer.queue_add(task_id="task1", spec_path="p1", max_attempts=2)
    emitter = EventEmitter(writer)
    writer.run_create(
        run_id="run-1",
        harness="claude",
        permission_mode="dontAsk",
        max_turns=80,
        base_branch="develop",
    )

    task1 = create_worktree(
        repo_path=repo,
        work_dir=work_dir,
        run_id="run-1",
        task_id="task1",
        spec_id="task1",
        base_branch="develop",
        harness="claude",
        writer=writer,
        emitter=emitter,
    )
    (task1.path / "g.txt").write_text("new file\n")
    _git(task1.path, "add", "g.txt")
    _git(task1.path, "commit", "-q", "-m", "task1: add g.txt")

    def gate_rerun() -> bool:
        raise AssertionError("the gate must never run when the first merge attempt succeeds")

    result = merge_task(
        repo_path=repo,
        worktree_path=task1.path,
        branch=task1.branch,
        base_branch="develop",
        task_id="task1",
        run_id="run-1",
        writer=writer,
        emitter=emitter,
        gate_rerun=gate_rerun,
        author=AUTHOR,
    )

    assert result.outcome.merged is True
    assert result.outcome.rebase_attempted is False
    assert result.worktree_removed is True
    assert not task1.path.exists()

    task_row = get_task(db_path, "task1")
    assert task_row is not None
    assert task_row.status == "done"
    assert task_row.worktree_path is None
    writer.close()


def test_precondition_violation_raises_rather_than_merging_the_wrong_state(
    tmp_path: Path,
) -> None:
    repo = _repo_on_develop(tmp_path)
    _git(repo, "checkout", "-q", "-b", "not-develop")

    with pytest.raises(MergeCommandError):
        attempt_merge_ladder(
            repo_path=repo,
            worktree_path=repo,
            branch="not-develop",
            base_branch="develop",
            gate_rerun=lambda: True,
            author=AUTHOR,
        )
