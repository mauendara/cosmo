"""Spec 9.5 log retention (`retention.apply_log_retention`, Phase 9): a
`DONE` task's harness logs age out after `log_retention.done_days`, a
`BLOCKED` task's after `blocked_days`; anything else (unknown task_id, or a
task still in flight) is left alone entirely."""

from __future__ import annotations

import os
import time
from pathlib import Path

from cosmo.config import CosmoConfig, load_config
from cosmo.retention import apply_log_retention
from cosmo.store import StoreWriter
from cosmo.store.enums import BlockedReason

NO_USER_CONFIG = Path("/nonexistent/config.toml")
_DAY = 86400


def _config(tmp_path: Path, **overrides: object) -> CosmoConfig:
    cfg = load_config(config_path=NO_USER_CONFIG)
    paths = cfg.paths.model_copy(
        update={
            "data_dir": tmp_path / "data",
            "work_dir": tmp_path / "work",
            "log_dir": tmp_path / "logs",
        }
    )
    updates: dict[str, object] = {"paths": paths}
    updates.update(overrides)
    return cfg.model_copy(update=updates)


def _write_log(cfg: CosmoConfig, task_id: str, age_days: float, *, now: float) -> Path:
    task_dir = cfg.paths.log_dir / "harness" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    log_file = task_dir / "abc123.ndjson"
    log_file.write_text('{"type": "system"}\n')
    mtime = now - age_days * _DAY
    os.utime(log_file, (mtime, mtime))
    return log_file


def test_a_done_tasks_old_log_is_removed_after_done_days(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    writer.queue_add(task_id="t1", spec_path="openspec/changes/t1", max_attempts=2)
    writer.queue_complete("t1")
    writer.close()

    now = time.time()
    log_file = _write_log(cfg, "t1", age_days=8, now=now)  # older than done_days=7

    summary = apply_log_retention(cfg, now=now)

    assert not log_file.exists()
    assert summary.files_removed == 1


def test_a_done_tasks_recent_log_is_kept(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    writer.queue_add(task_id="t1", spec_path="openspec/changes/t1", max_attempts=2)
    writer.queue_complete("t1")
    writer.close()

    now = time.time()
    log_file = _write_log(cfg, "t1", age_days=1, now=now)  # inside done_days=7

    summary = apply_log_retention(cfg, now=now)

    assert log_file.exists()
    assert summary.files_removed == 0


def test_a_blocked_tasks_log_survives_the_done_window_but_not_the_blocked_one(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    writer.queue_add(task_id="t1", spec_path="openspec/changes/t1", max_attempts=2)
    writer.queue_block("t1", BlockedReason.CODE_FAILURE)
    writer.close()

    now = time.time()
    # Older than done_days (7) but inside blocked_days (30) -- must survive.
    log_file = _write_log(cfg, "t1", age_days=10, now=now)

    summary = apply_log_retention(cfg, now=now)

    assert log_file.exists()
    assert summary.files_removed == 0

    # Now push it past blocked_days too.
    old_mtime = now - 31 * _DAY
    os.utime(log_file, (old_mtime, old_mtime))
    summary2 = apply_log_retention(cfg, now=now)
    assert not log_file.exists()
    assert summary2.files_removed == 1


def test_a_task_still_in_flight_is_never_touched(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    writer.queue_add(task_id="t1", spec_path="openspec/changes/t1", max_attempts=2)
    writer.close()  # left at 'queued' -- not terminal

    now = time.time()
    log_file = _write_log(cfg, "t1", age_days=999, now=now)

    summary = apply_log_retention(cfg, now=now)

    assert log_file.exists()
    assert summary.files_removed == 0


def test_an_unknown_task_id_directory_is_left_alone(tmp_path: Path) -> None:
    """No `task_queue` row to attribute the directory to at all -- e.g. a
    task purged from the queue by hand. Retention must never guess."""
    cfg = _config(tmp_path)
    StoreWriter(cfg.paths.db_path).close()  # just get a migrated db on disk

    now = time.time()
    log_file = _write_log(cfg, "ghost", age_days=999, now=now)

    summary = apply_log_retention(cfg, now=now)

    assert log_file.exists()
    assert summary.files_removed == 0


def test_empty_task_directories_are_removed_after_their_last_log_is_pruned(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path)
    writer = StoreWriter(cfg.paths.db_path)
    writer.queue_add(task_id="t1", spec_path="openspec/changes/t1", max_attempts=2)
    writer.queue_complete("t1")
    writer.close()

    now = time.time()
    _write_log(cfg, "t1", age_days=8, now=now)

    apply_log_retention(cfg, now=now)

    assert not (cfg.paths.log_dir / "harness" / "t1").exists()


def test_missing_harness_log_dir_is_a_clean_no_op(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    summary = apply_log_retention(cfg)
    assert summary.files_removed == 0
    assert summary.bytes_removed == 0
