"""Spec 6.1 layer 2 (the diff gate) against real `git` -- built in `tmp_path`
the same way `test_git_merge.py` builds its fixture repos, never touching
this repo or a real target repo.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from cosmo.config import load_config
from cosmo.config.model import GateConfig
from cosmo.gate.diffgate import run_diff_gate

AUTHOR = ("Cosmo Test", "cosmo-test@example.com")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
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
        check=True,
    )


def _repo_on_develop(tmp_path: Path) -> Path:
    repo = tmp_path / "target-repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "develop")
    test_file = repo / "src" / "test" / "FooTest.java"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "class FooTest {\n"
        "  void a() { assertThat(1).isEqualTo(1); }\n"
        "  void b() { assertThat(2).isEqualTo(2); }\n"
        "  void c() { assertThat(3).isEqualTo(3); }\n"
        "}\n"
    )
    src_file = repo / "src" / "main" / "Foo.java"
    src_file.parent.mkdir(parents=True)
    src_file.write_text("class Foo {}\n")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _gate_config() -> GateConfig:
    return load_config(config_path=Path("/nonexistent/config.toml")).gate


def test_diff_gate_passes_when_no_test_file_touched(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    _git(repo, "checkout", "-q", "-b", "task/spec-1")
    (repo / "src" / "main" / "Foo.java").write_text("class Foo { int x = 1; }\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "touch main only")

    result = run_diff_gate(
        worktree_path=repo,
        base_branch="develop",
        task_branch="task/spec-1",
        gate=_gate_config(),
        allow_test_edits=False,
    )
    assert result.passed
    assert result.violations == []


def test_diff_gate_fails_when_test_file_modified(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    _git(repo, "checkout", "-q", "-b", "task/spec-2")
    test_file = repo / "src" / "test" / "FooTest.java"
    test_file.write_text(test_file.read_text() + "  void d() { assertThat(4).isEqualTo(4); }\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add a test")

    result = run_diff_gate(
        worktree_path=repo,
        base_branch="develop",
        task_branch="task/spec-2",
        gate=_gate_config(),
        allow_test_edits=False,
    )
    assert not result.passed
    assert any(v.kind == "test_path_modified" for v in result.violations)


def test_diff_gate_detects_net_assertion_decrease(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    _git(repo, "checkout", "-q", "-b", "task/spec-3")
    test_file = repo / "src" / "test" / "FooTest.java"
    # Deliberately weakened: removes an assertion line, matching the plan's
    # own "a deliberately weakened test" exit-criterion scenario.
    weakened = test_file.read_text().replace("  void c() { assertThat(3).isEqualTo(3); }\n", "")
    test_file.write_text(weakened)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "weaken a test")

    result = run_diff_gate(
        worktree_path=repo,
        base_branch="develop",
        task_branch="task/spec-3",
        gate=_gate_config(),
        allow_test_edits=False,
    )
    assert not result.passed
    assert any(v.kind == "assertion_count_decreased" for v in result.violations)


def test_diff_gate_detects_skip_annotation(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    _git(repo, "checkout", "-q", "-b", "task/spec-4")
    test_file = repo / "src" / "test" / "FooTest.java"
    test_file.write_text(test_file.read_text() + "  @Disabled\n  void e() {}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "disable something")

    result = run_diff_gate(
        worktree_path=repo,
        base_branch="develop",
        task_branch="task/spec-4",
        gate=_gate_config(),
        allow_test_edits=False,
    )
    assert not result.passed
    assert any(v.kind == "skip_annotation_introduced" for v in result.violations)


def test_diff_gate_detects_deleted_test_file(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    _git(repo, "checkout", "-q", "-b", "task/spec-5")
    (repo / "src" / "test" / "FooTest.java").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "delete the test")

    result = run_diff_gate(
        worktree_path=repo,
        base_branch="develop",
        task_branch="task/spec-5",
        gate=_gate_config(),
        allow_test_edits=False,
    )
    assert not result.passed
    assert any(v.kind == "test_path_deleted" for v in result.violations)


def test_diff_gate_detects_loc_drop_beyond_threshold(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    test_file = repo / "src" / "test" / "FooTest.java"
    padded = "class FooTest {\n" + "".join(f"  void t{i}() {{}}\n" for i in range(30)) + "}\n"
    test_file.write_text(padded)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "pad the test file")  # lands directly on develop

    _git(repo, "checkout", "-q", "-b", "task/spec-6")
    trimmed = "class FooTest {\n" + "".join(f"  void t{i}() {{}}\n" for i in range(5)) + "}\n"
    test_file.write_text(trimmed)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "trim the test file a lot")

    result = run_diff_gate(
        worktree_path=repo,
        base_branch="develop",
        task_branch="task/spec-6",
        gate=_gate_config(),
        allow_test_edits=False,
    )
    assert not result.passed
    assert any(v.kind == "test_loc_dropped" for v in result.violations)


def test_allow_test_edits_bypasses_the_gate(tmp_path: Path) -> None:
    repo = _repo_on_develop(tmp_path)
    _git(repo, "checkout", "-q", "-b", "task/spec-7")
    (repo / "src" / "test" / "FooTest.java").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "delete the test, allowed")

    result = run_diff_gate(
        worktree_path=repo,
        base_branch="develop",
        task_branch="task/spec-7",
        gate=_gate_config(),
        allow_test_edits=True,
    )
    assert result.passed


def test_diff_gate_does_not_flag_a_newly_added_test_file(tmp_path: Path) -> None:
    """Spec 6.1 layer 2 says "modified or deleted" -- a brand-new test file
    is exactly what a well-behaved agent is expected to add for new work.
    Found by hand against a real fixture run: an earlier version of this
    gate rejected every task that added a new test at all."""
    repo = _repo_on_develop(tmp_path)
    _git(repo, "checkout", "-q", "-b", "task/spec-8")
    new_test = repo / "src" / "test" / "BarTest.java"
    new_test.write_text("class BarTest {\n  void a() { assertThat(1).isEqualTo(1); }\n}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add a brand new test")

    result = run_diff_gate(
        worktree_path=repo,
        base_branch="develop",
        task_branch="task/spec-8",
        gate=_gate_config(),
        allow_test_edits=False,
    )
    assert result.passed
    assert result.violations == []


def test_diff_gate_still_flags_a_disabled_newly_added_test(tmp_path: Path) -> None:
    """An added-but-immediately-disabled test is still suspicious even
    though it's a net-new file."""
    repo = _repo_on_develop(tmp_path)
    _git(repo, "checkout", "-q", "-b", "task/spec-9")
    new_test = repo / "src" / "test" / "BarTest.java"
    new_test.write_text(
        "class BarTest {\n  @Disabled\n  void a() { assertThat(1).isEqualTo(1); }\n}\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add a disabled new test")

    result = run_diff_gate(
        worktree_path=repo,
        base_branch="develop",
        task_branch="task/spec-9",
        gate=_gate_config(),
        allow_test_edits=False,
    )
    assert not result.passed
    assert any(v.kind == "skip_annotation_introduced" for v in result.violations)
