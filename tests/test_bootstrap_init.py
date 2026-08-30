"""`run_init` orchestration (spec 10.4 steps 1-7, plan Phase 4).

Runs against the real `openspec` binary (skipped if unavailable) and the
real shipped templates -- this is the one place Phase 4's own handoff
flagged as reasonable to exercise for real rather than fake (openspec init
was confirmed by hand to be fast, offline, and side-effect-free).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cosmo.bootstrap.init import GitBranchOutcome, run_init
from cosmo.config import CosmoConfig, load_config
from cosmo.store import StoreWriter
from cosmo.store.reader import find_project_by_path

NO_USER_CONFIG = Path("/nonexistent/config.toml")

pytestmark = pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)


def _config(tmp_path: Path) -> CosmoConfig:
    cfg = load_config(config_path=NO_USER_CONFIG)
    paths = cfg.paths.model_copy(
        update={"data_dir": tmp_path, "work_dir": tmp_path / "work", "log_dir": tmp_path / "logs"}
    )
    return cfg.model_copy(update={"paths": paths})


def _git_repo(tmp_path: Path) -> Path:
    target = tmp_path / "target-repo"
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    return target


def test_auto_inits_a_directory_that_is_not_a_git_repo(tmp_path: Path) -> None:
    target = tmp_path / "not-a-repo"
    target.mkdir()
    cfg = _config(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)

    result = run_init(
        target,
        harness="claude",
        project_template="_blank",
        base_branch="develop",
        force_docs=False,
        writer=writer,
        db_path=cfg.paths.db_path,
    )
    writer.close()

    assert result.git_branch is GitBranchOutcome.REPO_INITIALIZED_AND_BRANCH_CREATED
    assert (target / ".git").is_dir()
    current_branch = subprocess.run(
        ["git", "-C", str(target), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert current_branch == "develop"


def test_leaves_a_dirty_repo_missing_the_base_branch_alone(tmp_path: Path) -> None:
    target = tmp_path / "dirty-repo"
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    (target / "untracked.txt").write_text("uncommitted\n")
    cfg = _config(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)

    result = run_init(
        target,
        harness="claude",
        project_template="_blank",
        base_branch="develop",
        force_docs=False,
        writer=writer,
        db_path=cfg.paths.db_path,
    )
    writer.close()

    assert result.git_branch is GitBranchOutcome.SKIPPED_DIRTY
    branches = subprocess.run(
        ["git", "-C", str(target), "branch", "--list", "develop"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert branches.strip() == ""


def test_full_init_produces_every_documented_artifact(tmp_path: Path) -> None:
    target = _git_repo(tmp_path)
    cfg = _config(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)

    result = run_init(
        target,
        harness="claude",
        project_template="java-spring-react",
        base_branch="develop",
        force_docs=False,
        writer=writer,
        db_path=cfg.paths.db_path,
    )

    assert result.git_branch is GitBranchOutcome.BRANCH_CREATED
    assert (target / "openspec" / "changes").is_dir()
    assert (target / "docs" / "backend" / "architecture.md").is_file()
    assert (target / ".agent" / "claude" / "settings.json").is_file()
    assert (target / "CLAUDE.md").is_symlink()
    assert (target / ".claude").is_symlink()
    assert result.already_registered is False

    project = find_project_by_path(cfg.paths.db_path, str(target))
    assert project is not None
    assert project.project_id == result.project_id
    assert project.harness == "claude"
    assert project.project_template == "java-spring-react"

    events = writer.connection.execute(
        "SELECT event_type FROM events WHERE event_type = 'agent_assets.synced'"
    ).fetchall()
    assert len(events) == 1
    writer.close()


def test_rerun_skips_registration_and_reports_skipped_docs(tmp_path: Path) -> None:
    target = _git_repo(tmp_path)
    cfg = _config(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    run_init(
        target,
        harness="claude",
        project_template="_blank",
        base_branch="develop",
        force_docs=False,
        writer=writer,
        db_path=cfg.paths.db_path,
    )

    second = run_init(
        target,
        harness="claude",
        project_template="_blank",
        base_branch="develop",
        force_docs=False,
        writer=writer,
        db_path=cfg.paths.db_path,
    )

    assert second.git_branch is GitBranchOutcome.ALREADY_ON_BASE_BRANCH
    assert second.already_registered is True
    assert len(second.docs.skipped) > 0
    assert second.docs.created == []
    # .agent/ is refreshed wholesale regardless -- a second agent_assets.synced.
    events = writer.connection.execute(
        "SELECT event_type FROM events WHERE event_type = 'agent_assets.synced'"
    ).fetchall()
    assert len(events) == 2
    writer.close()
