"""`sync_harness_assets` (spec 10.5, plan Phase 4).

Uses an isolated fixture template tree rather than the real
`templates/harness/claude/`, so the wholesale-replace behavior can be
exercised precisely (a stale file that shouldn't survive a resync).
"""

from __future__ import annotations

from pathlib import Path

from cosmo.bootstrap.assets import sync_harness_assets
from cosmo.bootstrap.hashing import compute_template_version
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


def _fixture_templates_root(tmp_path: Path) -> Path:
    root = tmp_path / "templates"
    harness_dir = root / "harness" / "widget"
    (harness_dir / "hooks").mkdir(parents=True)
    (harness_dir / "CLAUDE.md").write_text("policy\n")
    (harness_dir / "hooks" / "guard.py").write_text("#!/usr/bin/env python3\n")
    return root


def test_sync_copies_the_template_tree_wholesale(tmp_path: Path) -> None:
    templates_root = _fixture_templates_root(tmp_path)
    target = tmp_path / "target-repo"
    target.mkdir()
    writer = StoreWriter(_config(tmp_path).paths.db_path)
    emitter = EventEmitter(writer)

    result = sync_harness_assets(target, "widget", emitter=emitter, templates_root=templates_root)

    dest = target / ".agent" / "widget"
    assert dest == result.dest
    assert (dest / "CLAUDE.md").read_text() == "policy\n"
    assert (dest / "hooks" / "guard.py").is_file()
    writer.close()


def test_resync_removes_a_stale_file_no_longer_in_the_template(tmp_path: Path) -> None:
    templates_root = _fixture_templates_root(tmp_path)
    target = tmp_path / "target-repo"
    target.mkdir()
    writer = StoreWriter(_config(tmp_path).paths.db_path)
    emitter = EventEmitter(writer)

    sync_harness_assets(target, "widget", emitter=emitter, templates_root=templates_root)
    stale = target / ".agent" / "widget" / "leftover-from-an-older-template-version.md"
    stale.write_text("stale")
    assert stale.is_file()

    sync_harness_assets(target, "widget", emitter=emitter, templates_root=templates_root)

    assert not stale.exists()
    writer.close()


def test_template_version_matches_the_source_tree_hash(tmp_path: Path) -> None:
    templates_root = _fixture_templates_root(tmp_path)
    target = tmp_path / "target-repo"
    target.mkdir()
    writer = StoreWriter(_config(tmp_path).paths.db_path)
    emitter = EventEmitter(writer)

    result = sync_harness_assets(target, "widget", emitter=emitter, templates_root=templates_root)

    expected = compute_template_version(templates_root / "harness" / "widget")
    assert result.template_version == expected
    writer.close()


def test_sync_emits_agent_assets_synced_with_the_documented_payload(tmp_path: Path) -> None:
    templates_root = _fixture_templates_root(tmp_path)
    target = tmp_path / "target-repo"
    target.mkdir()
    writer = StoreWriter(_config(tmp_path).paths.db_path)
    emitter = EventEmitter(writer)

    result = sync_harness_assets(
        target, "widget", emitter=emitter, run_id="run-7", templates_root=templates_root
    )

    row = writer.connection.execute(
        "SELECT event_type, severity, run_id, payload FROM events WHERE event_id = ?",
        (result.event_id,),
    ).fetchone()
    assert row["event_type"] == "agent_assets.synced"
    assert row["severity"] == "info"
    assert row["run_id"] == "run-7"
    import json

    payload = json.loads(row["payload"])
    assert payload["harness"] == "widget"
    assert payload["template_version"] == result.template_version
    assert payload["target_path"] == str(target)
    writer.close()


def test_sync_without_a_run_id_uses_the_run_less_scope(tmp_path: Path) -> None:
    """`cosmo init` (this phase) has no run_id yet -- Phase 1's `event_sequence`
    scoping (run_id or '') must accept a project-level, run-less sync."""
    templates_root = _fixture_templates_root(tmp_path)
    target = tmp_path / "target-repo"
    target.mkdir()
    writer = StoreWriter(_config(tmp_path).paths.db_path)
    emitter = EventEmitter(writer)

    result = sync_harness_assets(target, "widget", emitter=emitter, templates_root=templates_root)

    row = writer.connection.execute(
        "SELECT run_id, sequence FROM events WHERE event_id = ?", (result.event_id,)
    ).fetchone()
    assert row["run_id"] is None
    assert row["sequence"] == 1
    writer.close()
