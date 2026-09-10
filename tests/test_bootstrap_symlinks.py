"""Root-level harness symlinks (spec 10.2, plan Phase 4).

Exercised against the real `templates/harness/<harness>/` trees (synced via
`sync_harness_assets`) since `HARNESS_ROOT_LINKS` is keyed by real harness
name, not a fixture. Parametrized over both registered harnesses (v13 plan
Phase 5) -- `ori-claude`'s root links point into `.agent/ori-claude/` the
same shape `claude`'s do into `.agent/claude/`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cosmo.bootstrap.assets import sync_harness_assets
from cosmo.bootstrap.symlinks import create_root_symlinks
from cosmo.config import CosmoConfig, load_config
from cosmo.events import EventEmitter
from cosmo.store import StoreWriter

NO_USER_CONFIG = Path("/nonexistent/config.toml")
HARNESSES = ["claude", "ori-claude"]


def _config(tmp_path: Path) -> CosmoConfig:
    cfg = load_config(config_path=NO_USER_CONFIG)
    paths = cfg.paths.model_copy(
        update={"data_dir": tmp_path, "work_dir": tmp_path / "work", "log_dir": tmp_path / "logs"}
    )
    return cfg.model_copy(update={"paths": paths})


def _synced_target(tmp_path: Path, harness: str) -> Path:
    target = tmp_path / "target-repo"
    target.mkdir()
    writer = StoreWriter(_config(tmp_path).paths.db_path)
    sync_harness_assets(target, harness, emitter=EventEmitter(writer))
    writer.close()
    return target


@pytest.mark.parametrize("harness", HARNESSES)
def test_every_created_symlink_is_relative(tmp_path: Path, harness: str) -> None:
    target = _synced_target(tmp_path, harness)

    results = create_root_symlinks(target, harness)

    created = [r for r in results if r.status in ("created", "refreshed")]
    assert created, "expected at least one symlink to be created"
    for r in created:
        raw = os.readlink(r.link_path)
        assert not raw.startswith("/"), f"{r.link_name} -> {raw} is not relative"


@pytest.mark.parametrize("harness", HARNESSES)
def test_symlinks_resolve_to_the_real_agent_directory(tmp_path: Path, harness: str) -> None:
    target = _synced_target(tmp_path, harness)

    results = create_root_symlinks(target, harness)

    agent_dir = target / ".agent" / harness
    by_name = {r.link_name: r for r in results}
    assert by_name["CLAUDE.md"].link_path.resolve() == (agent_dir / "CLAUDE.md").resolve()
    assert by_name[".claude"].link_path.resolve() == agent_dir.resolve()
    assert by_name["agents"].link_path.resolve() == (agent_dir / "agents").resolve()
    assert by_name["skills"].link_path.resolve() == (agent_dir / "skills").resolve()


@pytest.mark.parametrize("harness", HARNESSES)
def test_all_four_spec_10_2_links_are_created(tmp_path: Path, harness: str) -> None:
    target = _synced_target(tmp_path, harness)

    results = create_root_symlinks(target, harness)

    statuses = {r.link_name: r.status for r in results}
    assert statuses == {
        "CLAUDE.md": "created",
        ".claude": "created",
        "agents": "created",
        "skills": "created",
    }


@pytest.mark.parametrize("harness", HARNESSES)
def test_rerunning_refreshes_rather_than_duplicates(tmp_path: Path, harness: str) -> None:
    target = _synced_target(tmp_path, harness)
    create_root_symlinks(target, harness)

    second = create_root_symlinks(target, harness)

    assert all(r.status == "refreshed" for r in second)


@pytest.mark.parametrize("harness", HARNESSES)
def test_a_real_file_at_a_link_path_is_not_clobbered(tmp_path: Path, harness: str) -> None:
    target = _synced_target(tmp_path, harness)
    (target / "CLAUDE.md").write_text("developer's own real file, not a symlink")

    results = create_root_symlinks(target, harness)

    claude_md = next(r for r in results if r.link_name == "CLAUDE.md")
    assert claude_md.status == "skipped_conflict"
    assert (target / "CLAUDE.md").read_text() == "developer's own real file, not a symlink"
    assert not (target / "CLAUDE.md").is_symlink()


def test_codex_creates_skill_link_and_nested_parent_without_dot_codex(tmp_path: Path) -> None:
    target = _synced_target(tmp_path, "codex")

    results = create_root_symlinks(target, "codex")

    assert {result.link_name: result.status for result in results} == {".agents/skills": "created"}
    assert not (target / ".codex").exists()
    assert (target / ".agents").is_dir()
    assert (target / ".agents" / "skills").resolve() == (
        target / ".agent" / "codex" / "skills"
    ).resolve()
    assert os.readlink(target / ".agents" / "skills") == "../.agent/codex/skills"


def test_codex_does_not_replace_non_cosmo_dot_codex_symlink(tmp_path: Path) -> None:
    target = _synced_target(tmp_path, "codex")
    elsewhere = target / "elsewhere"
    elsewhere.mkdir()
    os.symlink("elsewhere", target / ".codex")

    create_root_symlinks(target, "codex")

    assert os.readlink(target / ".codex") == "elsewhere"


def test_codex_removes_only_legacy_cosmo_dot_codex_symlink(tmp_path: Path) -> None:
    target = _synced_target(tmp_path, "codex")
    os.symlink(".agent/codex", target / ".codex")

    results = create_root_symlinks(target, "codex")

    legacy = next(result for result in results if result.link_name == ".codex")
    assert legacy.status == "removed_legacy"
    assert not (target / ".codex").exists()


def test_codex_does_not_follow_a_symlinked_agents_parent(tmp_path: Path) -> None:
    target = _synced_target(tmp_path, "codex")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, target / ".agents")

    results = create_root_symlinks(target, "codex")

    skills = next(result for result in results if result.link_name == ".agents/skills")
    assert skills.status == "skipped_conflict"
    assert not (outside / "skills").exists()


def test_codex_discovery_links_are_idempotent(tmp_path: Path) -> None:
    target = _synced_target(tmp_path, "codex")
    create_root_symlinks(target, "codex")

    results = create_root_symlinks(target, "codex")

    assert all(result.status == "refreshed" for result in results)
    assert os.readlink(target / ".agents" / "skills") == "../.agent/codex/skills"


def test_codex_preserves_user_owned_skills_directory(tmp_path: Path) -> None:
    target = _synced_target(tmp_path, "codex")
    user_skills = target / ".agents" / "skills"
    user_skills.mkdir(parents=True)
    (user_skills / "custom.md").write_text("mine\n")

    results = create_root_symlinks(target, "codex")

    skills = next(result for result in results if result.link_name == ".agents/skills")
    assert skills.status == "skipped_conflict"
    assert (user_skills / "custom.md").read_text() == "mine\n"
