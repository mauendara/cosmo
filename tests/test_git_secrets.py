"""The gitleaks pre-commit hook (spec 6.1, plan Phase 5).

Install/idempotency is tested unconditionally (pure filesystem mechanics).
The real-scan tests are skipped if `gitleaks` isn't on PATH -- the same
posture Phase 4 took toward `openspec` (see `test_bootstrap_init.py`).
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from cosmo.git.secrets import HOOK_MARKER, install_gitleaks_pre_commit_hook, run_gitleaks_scan

pytestmark_gitleaks = pytest.mark.skipif(
    shutil.which("gitleaks") is None, reason="real gitleaks CLI not on PATH"
)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def test_install_creates_an_executable_hook(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = install_gitleaks_pre_commit_hook(repo)
    assert result.status == "created"
    assert result.path == repo / ".git" / "hooks" / "pre-commit"
    assert result.path.is_file()
    assert HOOK_MARKER in result.path.read_text()
    mode = result.path.stat().st_mode
    assert mode & stat.S_IXUSR


def test_install_is_idempotent_and_refreshes_its_own_hook(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = install_gitleaks_pre_commit_hook(repo)
    assert first.status == "created"
    second = install_gitleaks_pre_commit_hook(repo)
    assert second.status == "refreshed"
    assert second.path.read_text() == first.path.read_text()


def test_install_never_clobbers_a_foreign_pre_commit_hook(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    foreign = hooks_dir / "pre-commit"
    foreign.write_text("#!/bin/sh\necho 'developer-owned hook'\n")

    result = install_gitleaks_pre_commit_hook(repo)

    assert result.status == "skipped_conflict"
    assert "developer-owned hook" in foreign.read_text()


def test_worktrees_of_the_same_repo_share_one_hooks_directory(tmp_path: Path) -> None:
    """The premise `install_gitleaks_pre_commit_hook` relies on: git hooks
    live in the common `.git/hooks/` dir, not per-worktree (confirmed by
    hand during Phase 5) -- installing once covers every worktree."""
    repo = _repo(tmp_path)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "base"], cwd=repo, check=True)
    subprocess.run(["git", "branch", "-M", "develop"], cwd=repo, check=True)
    wt = tmp_path / "wt1"
    subprocess.run(["git", "worktree", "add", "-q", str(wt), "-b", "task/x"], cwd=repo, check=True)

    install_gitleaks_pre_commit_hook(repo)

    assert (repo / ".git" / "hooks" / "pre-commit").is_file()
    assert not (wt / ".git").is_dir()  # a worktree's .git is a file, not a directory


@pytestmark_gitleaks
def test_installed_hook_blocks_a_commit_containing_a_secret(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    install_gitleaks_pre_commit_hook(repo)

    (repo / "secret.txt").write_text('AWS_SECRET_ACCESS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    subprocess.run(["git", "add", "secret.txt"], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "commit", "-q", "-m", "add secret"], cwd=repo, capture_output=True, text=True
    )

    assert result.returncode != 0
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True, check=False
    )
    assert log.stdout.strip() == ""


@pytestmark_gitleaks
def test_installed_hook_allows_a_clean_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    install_gitleaks_pre_commit_hook(repo)

    (repo / "clean.txt").write_text("hello world\n")
    subprocess.run(["git", "add", "clean.txt"], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "commit", "-q", "-m", "add clean file"], cwd=repo, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr


@pytestmark_gitleaks
def test_scan_excludes_a_gitignored_build_artifact(tmp_path: Path) -> None:
    """G5 (docs/v15-fixes-after-wa-chat-run.md): reproduces the real
    `wa-chat-storybook-vr` incident -- a gitignored, never-committed
    `storybook-static/` build artifact false-flagging gitleaks'
    `generic-api-key` rule on minified vendor JS noise, blocking an
    otherwise-correct task."""
    repo = _repo(tmp_path)
    (repo / ".gitignore").write_text("storybook-static/\n")
    build_dir = repo / "storybook-static"
    build_dir.mkdir()
    # Mimics the real false-positive shape: a bare assignment gitleaks'
    # generic-api-key rule matches on minified vendor JS, no real secret.
    (build_dir / "bundle.js").write_text(
        'var o=e.childrens,k="sk_live_" + "abcdefghijklmnopqrstuvwx";\n'
    )

    result = run_gitleaks_scan(repo)

    assert result.clean, result.findings


@pytestmark_gitleaks
def test_scan_still_catches_a_real_secret_in_a_tracked_file(tmp_path: Path) -> None:
    """The G5 exclusion must not become a blanket bypass -- a real secret
    outside any gitignored/build-output path still fails the scan."""
    repo = _repo(tmp_path)
    (repo / "config.py").write_text('AWS_SECRET_ACCESS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')

    result = run_gitleaks_scan(repo)

    assert not result.clean
    assert result.findings
