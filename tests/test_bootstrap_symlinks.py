"""Root-level harness symlinks (spec 10.2, plan Phase 4).

Exercised against the real `templates/harness/claude/` tree (synced via
`sync_harness_assets`) since `HARNESS_ROOT_LINKS` is keyed by real harness
name, not a fixture.
"""

from __future__ import annotations

import os
from pathlib import Path

from cosmo.bootstrap.assets import sync_harness_assets
from cosmo.bootstrap.symlinks import create_root_symlinks
from cosmo.config import CosmoConfig, load_config
from cosmo.events import EventEmitter
from cosmo.store import StoreWriter

NO_USER_CONFIG = Path("/nonexistent/config.toml")


def _config(tmp_path: Path) -> CosmoConfig:
    cfg = load_config(config_path=NO_USER_CONFIG)
    paths = cfg.paths.model_copy(
        update={"data_dir": tmp_path, "work_dir": tmp_path / "work", "log_dir": tmp_path / "logs"}
    )
    return cfg.model_copy(update={"paths": paths})


def _synced_target(tmp_path: Path) -> Path:
    target = tmp_path / "target-repo"
    target.mkdir()
    writer = StoreWriter(_config(tmp_path).paths.db_path)
    sync_harness_assets(target, "claude", emitter=EventEmitter(writer))
    writer.close()
    return target


def test_every_created_symlink_is_relative(tmp_path: Path) -> None:
    target = _synced_target(tmp_path)

    results = create_root_symlinks(target, "claude")

    created = [r for r in results if r.status in ("created", "refreshed")]
    assert created, "expected at least one symlink to be created"
    for r in created:
        raw = os.readlink(r.link_path)
        assert not raw.startswith("/"), f"{r.link_name} -> {raw} is not relative"


def test_symlinks_resolve_to_the_real_agent_directory(tmp_path: Path) -> None:
    target = _synced_target(tmp_path)

    results = create_root_symlinks(target, "claude")

    agent_dir = target / ".agent" / "claude"
    by_name = {r.link_name: r for r in results}
    assert by_name["CLAUDE.md"].link_path.resolve() == (agent_dir / "CLAUDE.md").resolve()
    assert by_name[".claude"].link_path.resolve() == agent_dir.resolve()
    assert by_name["agents"].link_path.resolve() == (agent_dir / "agents").resolve()
    assert by_name["skills"].link_path.resolve() == (agent_dir / "skills").resolve()


def test_all_four_spec_10_2_links_are_created(tmp_path: Path) -> None:
    target = _synced_target(tmp_path)

    results = create_root_symlinks(target, "claude")

    statuses = {r.link_name: r.status for r in results}
    assert statuses == {
        "CLAUDE.md": "created",
        ".claude": "created",
        "agents": "created",
        "skills": "created",
    }


def test_rerunning_refreshes_rather_than_duplicates(tmp_path: Path) -> None:
    target = _synced_target(tmp_path)
    create_root_symlinks(target, "claude")

    second = create_root_symlinks(target, "claude")

    assert all(r.status == "refreshed" for r in second)


def test_a_real_file_at_a_link_path_is_not_clobbered(tmp_path: Path) -> None:
    target = _synced_target(tmp_path)
    (target / "CLAUDE.md").write_text("developer's own real file, not a symlink")

    results = create_root_symlinks(target, "claude")

    claude_md = next(r for r in results if r.link_name == "CLAUDE.md")
    assert claude_md.status == "skipped_conflict"
    assert (target / "CLAUDE.md").read_text() == "developer's own real file, not a symlink"
    assert not (target / "CLAUDE.md").is_symlink()
