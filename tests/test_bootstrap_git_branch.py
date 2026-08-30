"""`cosmo.bootstrap.git_branch.commit_bootstrap_output` -- the fix for
`cosmo init` never committing its own bootstrap output (found live: a
freshly initialized repo's `openspec/`/`docs/`/`.agent/` sat uncommitted,
so the very first task ever run against it hit `MERGING`'s own
`_assert_ready` refusing to merge onto a dirty `repo_path`)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from cosmo.bootstrap.git_branch import commit_bootstrap_output


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _repo_with_identity(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@example.com")
    return repo


def test_commits_untracked_and_modified_files(tmp_path: Path) -> None:
    repo = _repo_with_identity(tmp_path)
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "README.md").write_text("hello, modified\n")
    (repo / "new-file.txt").write_text("new\n")

    committed = commit_bootstrap_output(repo)

    assert committed is True
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""
    assert "init bootstrap" in _git(repo, "log", "-1", "--format=%s").stdout


def test_is_a_no_op_on_a_clean_tree(tmp_path: Path) -> None:
    repo = _repo_with_identity(tmp_path)
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base")
    before = _git(repo, "log", "-1", "--format=%H").stdout

    committed = commit_bootstrap_output(repo)

    assert committed is False
    after = _git(repo, "log", "-1", "--format=%H").stdout
    assert before == after


def test_commits_the_very_first_commit_on_an_unborn_branch(tmp_path: Path) -> None:
    """The exact scenario found live: a freshly `git init`-ed repo with zero
    commits, right after `create_and_checkout_branch` -- `working_tree_is_
    clean` must still correctly see the untracked bootstrap files as dirty
    even though HEAD has no commit to diff against yet."""
    repo = tmp_path / "fresh"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@example.com")
    (repo / "openspec-placeholder.txt").write_text("bootstrap output\n")

    committed = commit_bootstrap_output(repo)

    assert committed is True
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""
    log = _git(repo, "log", "--oneline")
    assert len(log.stdout.strip().splitlines()) == 1
