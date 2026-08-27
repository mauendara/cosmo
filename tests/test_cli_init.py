"""`cosmo init` / `cosmo templates list` (spec 10.4, 10.3, plan Phase 4 exit
criteria)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cosmo.bootstrap.git_identity import GitIdentity, read_configured_identity
from cosmo.cli.main import app
from cosmo.config import load_config

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    # No global git identity on the host running this test, regardless of
    # what this dev box's own ~/.gitconfig actually has -- the new git
    # identity step (spec 3.4 extended) must see a clean slate.
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))


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


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_auto_inits_a_non_git_directory(tmp_path: Path) -> None:
    target = tmp_path / "plain-dir"
    target.mkdir()
    # "\n" accepts the (now-interactive) default git-identity prompt below.
    result = runner.invoke(app, ["init", str(target)], input="\n")
    assert result.exit_code == 0, result.stdout
    assert (target / ".git").is_dir()
    current_branch = subprocess.run(
        ["git", "-C", str(target), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert current_branch == "develop"
    combined = " ".join(result.stdout.split())
    assert "git init" in combined and "develop" in combined


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_against_a_scratch_git_repo_produces_every_documented_artifact(
    tmp_path: Path,
) -> None:
    target = _git_repo(tmp_path)

    result = runner.invoke(
        app, ["init", str(target), "--project-template", "java-spring-react"], input="\n"
    )

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
    runner.invoke(app, ["init", str(target)], input="\n")
    stale = target / ".agent" / "claude" / "no-longer-in-the-template.txt"
    stale.write_text("stale")

    result = runner.invoke(app, ["init", str(target)], input="n\n")

    assert result.exit_code == 0, result.stdout
    assert "skipped" in result.stdout
    assert "already registered" in result.stdout
    assert not stale.exists()


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_seeds_the_config_default_identity_when_none_exists(tmp_path: Path) -> None:
    target = _git_repo(tmp_path)

    # "\n" accepts the default via the (now-interactive) prompt's own default=True.
    result = runner.invoke(app, ["init", str(target)], input="\n")

    assert result.exit_code == 0, result.stdout
    assert "No git identity configured" in result.stdout
    assert "git identity" in result.stdout and "config default" in result.stdout
    assert read_configured_identity(target) == GitIdentity(
        name="Cosmo", email="cosmo@entropiainversa.com"
    )


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_declining_the_default_identity_prompts_for_one_instead(tmp_path: Path) -> None:
    target = _git_repo(tmp_path)

    result = runner.invoke(app, ["init", str(target)], input="n\nJane Dev\njane@example.com\n")

    assert result.exit_code == 0, result.stdout
    assert read_configured_identity(target) == GitIdentity(
        name="Jane Dev", email="jane@example.com"
    )


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_declining_to_replace_an_existing_identity_leaves_it_untouched(
    tmp_path: Path,
) -> None:
    target = _git_repo(tmp_path)
    runner.invoke(app, ["init", str(target)], input="\n")  # seeds the config default

    result = runner.invoke(app, ["init", str(target)], input="n\n")

    assert result.exit_code == 0, result.stdout
    assert read_configured_identity(target) == GitIdentity(
        name="Cosmo", email="cosmo@entropiainversa.com"
    )


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_confirming_replaces_an_existing_identity_with_the_prompted_one(
    tmp_path: Path,
) -> None:
    target = _git_repo(tmp_path)
    runner.invoke(app, ["init", str(target)], input="\n")  # seeds the config default

    result = runner.invoke(app, ["init", str(target)], input="y\nJane Dev\njane@example.com\n")

    assert result.exit_code == 0, result.stdout
    assert read_configured_identity(target) == GitIdentity(
        name="Jane Dev", email="jane@example.com"
    )


@pytest.mark.skipif(
    subprocess.run(["which", "openspec"], capture_output=True, check=False).returncode != 0,
    reason="real openspec CLI not on PATH",
)
def test_init_explicit_git_author_flags_skip_the_prompt_entirely(tmp_path: Path) -> None:
    target = _git_repo(tmp_path)

    result = runner.invoke(
        app,
        [
            "init",
            str(target),
            "--git-author-name",
            "CI Bot",
            "--git-author-email",
            "ci@example.com",
        ],
        # No input at all -- if this hit a prompt, CliRunner would error on
        # end-of-input rather than silently succeed.
        input="",
    )

    assert result.exit_code == 0, result.stdout
    assert read_configured_identity(target) == GitIdentity(name="CI Bot", email="ci@example.com")
