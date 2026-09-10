"""Codex adapter contract, role prompts, and registry-facing capabilities."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from cosmo.checks import CheckStatus
from cosmo.config import CosmoConfig, load_config
from cosmo.config.model import HarnessModelOverrides
from cosmo.harness.base import HarnessResult
from cosmo.harness.codex import CodexAdapter
from cosmo.harness.codex.invoker import CodexInvoker
from cosmo.task.review import REVIEW_RESULT_RELATIVE_PATH

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_CODEX = FIXTURES / "fake_codex.sh"
NO_USER_CONFIG = Path("/nonexistent/config.toml")


def _config(tmp_path: Path) -> CosmoConfig:
    config = load_config(config_path=NO_USER_CONFIG)
    paths = config.paths.model_copy(
        update={"data_dir": tmp_path, "work_dir": tmp_path / "work", "log_dir": tmp_path / "logs"}
    )
    return config.model_copy(update={"paths": paths})


def _adapter(tmp_path: Path, config: CosmoConfig | None = None) -> CodexAdapter:
    return CodexAdapter(config or _config(tmp_path), cwd=tmp_path, binary=str(FAKE_CODEX))


def _result() -> HarnessResult:
    return HarnessResult(
        success=True,
        output_summary="turn completed",
        raw_log_path=None,
        files_changed=[],
        duration_seconds=0.1,
        total_cost_usd=None,
        exit_code=0,
        session_id="thread-1",
    )


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    captured: list[dict[str, object]] = []

    def _invoke(
        self: CodexInvoker,
        *,
        task_id: str,
        prompt: str,
        model: str,
        role: str,
        on_activity: Callable[[str], None] | None = None,
    ) -> HarnessResult:
        captured.append(
            {
                "task_id": task_id,
                "prompt": prompt,
                "model": model,
                "role": role,
                "on_activity": on_activity,
            }
        )
        return _result()

    monkeypatch.setattr(CodexInvoker, "invoke", _invoke)
    return captured


def test_declares_validated_capabilities_honestly() -> None:
    caps = CodexAdapter.capabilities
    assert caps.reports_native_progress is False
    assert caps.supports_retry_context is True
    assert caps.has_internal_timeout is False
    assert caps.reports_native_cost is False
    assert caps.supports_gating is True
    assert caps.supports_structured_stream is True


def test_preflight_checks_binary_without_running_it(tmp_path: Path) -> None:
    before = set(tmp_path.rglob("*"))

    results = _adapter(tmp_path).preflight()

    assert next(result for result in results if result.name == "codex cli").status is CheckStatus.OK
    assert set(tmp_path.rglob("*")) == before


def test_preflight_fails_when_api_billing_key_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_API_KEY", "must-not-be-used")

    billing = next(
        result for result in _adapter(tmp_path).preflight() if result.name == "subscription billing"
    )

    assert billing.status is CheckStatus.FAIL
    assert billing.blocking


def test_preflight_fails_closed_for_unsupported_permission_mode(tmp_path: Path) -> None:
    config = _config(tmp_path)
    harness = config.harness.model_copy(update={"permission_mode": "auto"})

    mode = next(
        result
        for result in _adapter(tmp_path, config.model_copy(update={"harness": harness})).preflight()
        if result.name == "permission mode"
    )

    assert mode.status is CheckStatus.FAIL


def test_probe_delegates_activity_and_explicit_model(
    tmp_path: Path, calls: list[dict[str, object]]
) -> None:
    activity: list[str] = []

    _adapter(tmp_path).probe("hello", model="gpt-probe", on_activity=activity.append)

    assert calls == [
        {
            "task_id": "probe",
            "prompt": "hello",
            "model": "gpt-probe",
            "role": "probe",
            "on_activity": activity.append,
        }
    ]


def test_propose_pins_exact_spec_id_and_uses_role_model(
    tmp_path: Path, calls: list[dict[str, object]]
) -> None:
    config = _config(tmp_path)
    harness = config.harness.model_copy(update={"propose_model": "gpt-propose"})
    adapter = _adapter(tmp_path, config.model_copy(update={"harness": harness}))

    adapter.propose(
        Path("docs/specs/app/tasks/scaffold-app-task.md"),
        {"task_id": "app-scaffold", "spec_id": "scaffold-app"},
    )

    assert calls[0]["task_id"] == "app-scaffold"
    assert calls[0]["role"] == "propose"
    assert calls[0]["model"] == "gpt-propose"
    assert calls[0]["prompt"] == (
        "Run OpenSpec's propose workflow for the change at "
        "docs/specs/app/tasks/scaffold-app-task.md. Name the change exactly "
        "'scaffold-app' (`openspec new change scaffold-app`) -- do not pick a different "
        "name, even a shorter or more natural-looking one. Follow this repository's "
        "operating policy for how to invoke OpenSpec."
    )


def test_propose_falls_back_to_spec_path_stem(
    tmp_path: Path, calls: list[dict[str, object]]
) -> None:
    _adapter(tmp_path).propose(Path("openspec/changes/add-foo"), {})

    assert calls[0]["task_id"] == "add-foo"
    assert "`openspec new change add-foo`" in str(calls[0]["prompt"])


def test_implement_always_inspects_existing_work_and_appends_retry_context(
    tmp_path: Path, calls: list[dict[str, object]]
) -> None:
    config = _config(tmp_path)
    harness = config.harness.model_copy(update={"implement_model": "gpt-implement"})
    adapter = _adapter(tmp_path, config.model_copy(update={"harness": harness}))

    adapter.implement("task-1", Path("openspec/changes/task-1"), "tests failed")

    assert calls[0]["role"] == "implement"
    assert calls[0]["model"] == "gpt-implement"
    assert calls[0]["prompt"] == (
        "Implement the OpenSpec change at openspec/changes/task-1 (task task-1). Before "
        "changing anything, inspect the current worktree with `git log`, `git status`, and "
        "`openspec status --change <id>` so you continue from work that is already present "
        "instead of redoing it. Do not run `git add` or `git commit`: Codex's workspace "
        "sandbox protects linked-worktree Git metadata. Leave completed implementation "
        "and OpenSpec changes in the working tree; Cosmo stages and commits them after "
        "this call returns."
        "\n\nThe previous attempt failed:\ntests failed"
    )


def test_review_is_fresh_read_only_and_names_the_canonical_absolute_verdict(
    tmp_path: Path, calls: list[dict[str, object]]
) -> None:
    config = _config(tmp_path)
    harness = config.harness.model_copy(update={"review_model": "gpt-review"})
    adapter = _adapter(tmp_path, config.model_copy(update={"harness": harness}))

    adapter.review("task-1", Path("openspec/changes/task-1"), "develop")

    assert calls[0]["role"] == "review"
    assert calls[0]["model"] == "gpt-review"
    assert calls[0]["prompt"] == (
        "Review this branch's implementation for task task-1. Run `git diff "
        "develop...HEAD` to see the diff and read the OpenSpec change at "
        "openspec/changes/task-1 (its spec/tasks.md) for what was asked. You have no "
        "memory of the implementation session; judge only what these show. Do not modify "
        "source files or any other repository content. When done, write only your verdict "
        f"to the canonical path `{tmp_path / REVIEW_RESULT_RELATIVE_PATH}` as JSON: "
        '`{"verdict": "approved"}` or `{"verdict": "rejected", "reason": "<why, '
        'specific enough to act on>"}`.'
    )


def test_harness_specific_role_model_override_wins(
    tmp_path: Path, calls: list[dict[str, object]]
) -> None:
    config = _config(tmp_path)
    override = HarnessModelOverrides(implement_model="gpt-codex-implement")
    harness = config.harness.model_copy(
        update={
            "implement_model": "global-implement",
            "overrides": {"codex": override},
        }
    )

    _adapter(tmp_path, config.model_copy(update={"harness": harness})).implement(
        "task-1", Path("openspec/changes/task-1")
    )

    assert calls[0]["model"] == "gpt-codex-implement"


def test_task_runner_cwd_rebind_reaches_composed_invoker(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    adapter.cwd = worktree

    adapter.probe("hello", model="gpt-probe")

    assert adapter._invoker.cwd == worktree


def test_progress_uses_core_file_watching_fallback(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError, match="tasks.md"):
        _adapter(tmp_path).get_progress("task-1")


def test_cancel_delegates_to_the_invoker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cancelled: list[str] = []
    monkeypatch.setattr(CodexInvoker, "cancel", lambda self, task_id: cancelled.append(task_id))

    _adapter(tmp_path).cancel("task-1")

    assert cancelled == ["task-1"]
