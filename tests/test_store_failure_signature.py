"""`store.failure_signature` (v5 improvements plan part 5, Class 1):
deterministic substring matching, no model call."""

from __future__ import annotations

from cosmo.store.failure_signature import classify_failure_signature


def test_none_error_detail_is_unmatched() -> None:
    assert classify_failure_signature(None) is None


def test_empty_error_detail_is_unmatched() -> None:
    assert classify_failure_signature("") is None


def test_missing_lockfile_shape() -> None:
    detail = "npm ERR! The `npm ci` command can only install with an existing package-lock.json"
    assert classify_failure_signature(detail) == "missing_lockfile"


def test_node_engine_mismatch_shape() -> None:
    detail = 'npm WARN EBADENGINE Unsupported engine {"package": "foo@2.0.0"}'
    assert classify_failure_signature(detail) == "node_engine_mismatch"


def test_enoent_node_modules_shape() -> None:
    detail = "Error: ENOENT: no such file or directory, open 'node_modules/.bin/vite'"
    assert classify_failure_signature(detail) == "enoent_node_modules"


def test_unrelated_error_stays_unmatched() -> None:
    assert classify_failure_signature("AssertionError: expected 1 but was 2") is None
