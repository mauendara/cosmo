"""`cosmo init` / `cosmo templates list` (spec 10.4, 10.3, plan Phase 4 exit
criteria)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cosmo.cli.main import app
from cosmo.config import load_config

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def _db_path() -> Path:
    return load_config().paths.db_path


def _git_repo(tmp_path: Path) -> Path:
    target = tmp_path / "target-repo"
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    return target


def test_templates_list_shows_the_real_shipped_templates() -> None:
    result = runner.invoke(app, ["templates", "list"])
    assert result.exit_code == 0
    assert "claude" in result.stdout
    assert "_blank" in result.stdout
    assert "java-spring-react" in result.stdout


def test_init_refuses_a_non_git_directory(tmp_path: Path) -> None:
    target = tmp_path / "plain-dir"
    target.mkdir()
    result = runner.invoke(app, ["init", str(target)])
    assert result.exit_code == 2
    # Rich may wrap the message across lines, so match on collapsed whitespace.
    combined = " ".join((result.stdout + result.stderr).split())
    assert "is not a git repository" in combined


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_against_a_scratch_git_repo_produces_every_documented_artifact(
    tmp_path: Path,
) -> None:
    target = _git_repo(tmp_path)

    result = runner.invoke(app, ["init", str(target), "--project-template", "java-spring-react"])

    assert result.exit_code == 0, result.stdout
    assert (target / "openspec" / "changes").is_dir()
    assert (target / "docs" / "base-standards.md").is_file()
    assert (target / ".agent" / "claude" / "settings.json").is_file()
    assert (target / "CLAUDE.md").is_symlink()
    assert "registered" in result.stdout


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_rerunning_init_reports_skipped_docs_and_refreshes_agent_dir(tmp_path: Path) -> None:
    target = _git_repo(tmp_path)
    runner.invoke(app, ["init", str(target)])
    stale = target / ".agent" / "claude" / "no-longer-in-the-template.txt"
    stale.write_text("stale")

    result = runner.invoke(app, ["init", str(target)])

    assert result.exit_code == 0, result.stdout
    assert "skipped" in result.stdout
    assert "already registered" in result.stdout
    assert not stale.exists()
