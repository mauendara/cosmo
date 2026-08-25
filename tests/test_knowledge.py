"""`cosmo.knowledge`: spec 11's `COMMITTING`-step guardrails -- the line-cap
enforcement (`caps.py`) and the structured `decisions-log.md` entry
(`decisions_log.py`)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from cosmo.knowledge.caps import docs_md_files, files_over_cap
from cosmo.knowledge.decisions_log import append_decision_entry


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _repo_on_develop(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "init", "-q")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "base")
    _git(repo, "branch", "-M", "develop")
    return repo


def test_docs_md_files_finds_only_docs_markdown_touched_on_the_branch(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    _git(repo, "checkout", "-qb", "task/x")
    (repo / "docs").mkdir()
    (repo / "docs" / "architecture.md").write_text("# Architecture\n")
    (repo / "backend").mkdir()
    (repo / "backend" / "App.java").write_text("class App {}\n")
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "-m",
        "add docs and code",
    )

    touched = docs_md_files(repo, "develop", "task/x")

    assert touched == ["docs/architecture.md"]


def test_docs_md_files_empty_when_branch_has_no_new_commits(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    _git(repo, "checkout", "-qb", "task/x")

    assert docs_md_files(repo, "develop", "task/x") == []


def test_files_over_cap_flags_only_files_exceeding_the_line_count(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    small = worktree / "small.md"
    small.write_text("\n".join(f"line {i}" for i in range(5)))
    big = worktree / "big.md"
    big.write_text("\n".join(f"line {i}" for i in range(500)))

    over = files_over_cap(worktree, ["small.md", "big.md"], max_lines=400)

    assert over == ["big.md"]


def test_files_over_cap_skips_a_path_that_no_longer_exists(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()

    over = files_over_cap(worktree, ["gone.md"], max_lines=400)

    assert over == []


def test_append_decision_entry_creates_the_file_with_a_header_then_appends(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()

    path = append_decision_entry(
        worktree, task_id="add-foo", spec_path="openspec/changes/add-foo", when="2026-01-01"
    )

    assert path == worktree / "docs" / "decisions-log.md"
    text = path.read_text()
    assert "# Decisions Log" in text
    assert "- 2026-01-01 | add-foo | openspec/changes/add-foo" in text


def test_append_decision_entry_appends_without_duplicating_the_header(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()

    append_decision_entry(worktree, task_id="task-1", spec_path="s1", when="2026-01-01")
    append_decision_entry(worktree, task_id="task-2", spec_path="s2", when="2026-01-02")

    text = (worktree / "docs" / "decisions-log.md").read_text()
    assert text.count("# Decisions Log") == 1
    assert "- 2026-01-01 | task-1 | s1" in text
    assert "- 2026-01-02 | task-2 | s2" in text
