"""Process and result mechanics for the non-interactive Codex CLI."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from cosmo.config import CosmoConfig
from cosmo.events import EventEmitter
from cosmo.harness.base import HarnessResult
from cosmo.harness.codex.stream import (
    ClassifiedEvent,
    ClassifiedKind,
    StreamReader,
    describe_activity,
)
from cosmo.proc import ManagedProcess, cancel_and_reap

BINARY = "codex"
BILLING_ENV_VAR = "CODEX_API_KEY"
TASK_ID_ENV_VAR = "COSMO_TASK_ID"
DB_PATH_ENV_VAR = "COSMO_DB_PATH"
ROLE_ENV_VAR = "COSMO_HARNESS_ROLE"

_HOOKS: tuple[tuple[str, str], ...] = (
    ("^(apply_patch|Bash)$", "test_path_guard.py"),
    ("^(apply_patch|Bash)$", "annotation_guard.py"),
    ("^(apply_patch|Bash)$", "commit_integrity_guard.py"),
    ("^Bash$", "background_task_guard.py"),
    ("^(apply_patch|Bash)$", "review_write_guard.py"),
    ("^(Read|Bash)$", "secret_read_guard.py"),
)


class CodexInvoker:
    """Own one fresh ``codex exec`` process per call and its cancellation."""

    def __init__(
        self,
        config: CosmoConfig,
        *,
        cwd: Path | None = None,
        binary: str = BINARY,
        run_id: str | None = None,
        emitter: EventEmitter | None = None,
    ) -> None:
        self.config = config
        self.cwd = cwd if cwd is not None else Path.cwd()
        self._binary = binary
        self._run_id = run_id
        self._emitter = emitter
        self._lock = threading.Lock()
        self._running: dict[str, ManagedProcess] = {}

    def build_argv(self, prompt: str, model: str) -> list[str]:
        argv = [
            self._binary,
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--strict-config",
            "--sandbox",
            "workspace-write",
            "--dangerously-bypass-hook-trust",
            "-C",
            str(self.cwd),
            "--model",
            model,
            "-c",
            'approval_policy="never"',
            "-c",
            'web_search="disabled"',
            "-c",
            "apps._default.enabled=false",
            "--disable",
            "apps",
            "--disable",
            "plugins",
            "--disable",
            "multi_agent",
            "--disable",
            "browser_use",
            "--disable",
            "computer_use",
            "--disable",
            "image_generation",
            "--disable",
            "skill_mcp_dependency_install",
            "-c",
            "developer_instructions="
            + json.dumps("Read and follow .agent/codex/CODEX.md before acting."),
            *self._hook_config_args(),
            prompt,
        ]
        assert "--dangerously-bypass-approvals-and-sandbox" not in argv
        assert "danger-full-access" not in argv
        return argv

    def build_env(self, task_id: str, role: str) -> dict[str, str]:
        env = dict(os.environ)
        env.pop(BILLING_ENV_VAR, None)
        env[TASK_ID_ENV_VAR] = task_id
        env[DB_PATH_ENV_VAR] = str(self.config.paths.db_path)
        env[ROLE_ENV_VAR] = role
        return env

    def invoke(
        self,
        *,
        task_id: str,
        prompt: str,
        model: str,
        role: str,
        on_activity: Callable[[str], None] | None = None,
    ) -> HarnessResult:
        raw_log_path = (
            self.config.paths.log_dir / "harness" / task_id / f"{uuid.uuid4().hex}.ndjson"
        )
        stderr_log_path = raw_log_path.with_suffix(".stderr")
        reader = StreamReader(
            on_event=_relay_activity(on_activity, cwd=self.cwd) if on_activity else None
        )
        process = ManagedProcess(
            self.build_argv(prompt, model),
            raw_log_path=raw_log_path,
            stderr_log_path=stderr_log_path,
            cwd=self.cwd,
            env=self.build_env(task_id, role),
            on_stdout_chunk=reader.feed,
        )
        with self._lock:
            self._running[task_id] = process
        started = time.monotonic()
        try:
            exit_code = process.wait()
        finally:
            process.cancel(grace_s=self.config.timeouts.kill_grace)
            with self._lock:
                self._running.pop(task_id, None)
        success = exit_code == 0
        return HarnessResult(
            success=success,
            output_summary=_summarize(reader, exit_code),
            raw_log_path=raw_log_path,
            files_changed=[],
            duration_seconds=time.monotonic() - started,
            total_cost_usd=None,
            exit_code=exit_code,
            session_id=reader.session_id,
            quota_window=None,
            quota_resets_at=None,
            tool_call_count=reader.tool_call_count,
        )

    def cancel(self, task_id: str) -> None:
        with self._lock:
            process = self._running.get(task_id)
        if process is None:
            return
        if self._emitter is None:
            process.cancel(grace_s=self.config.timeouts.kill_grace)
        else:
            cancel_and_reap(
                process,
                run_id=self._run_id or "",
                task_id=task_id,
                worktree_path=self.cwd,
                config=self.config,
                emitter=self._emitter,
            )

    def _hook_config_args(self) -> list[str]:
        hooks_dir = self.cwd / ".agent" / "codex" / "hooks"
        entries = []
        for matcher, filename in _HOOKS:
            command = f"python3 {json.dumps(str(hooks_dir / filename))}"
            entries.append(
                "{matcher="
                + json.dumps(matcher)
                + ',hooks=[{type="command",command='
                + json.dumps(command)
                + "}]}"
            )
        return ["-c", "hooks.PreToolUse=[" + ",".join(entries) + "]"]


def stderr_log_path(raw_log_path: Path) -> Path:
    """Return the separately retained stderr evidence path for a result log."""
    return raw_log_path.with_suffix(".stderr")


def _relay_activity(
    on_activity: Callable[[str], None], *, cwd: Path
) -> Callable[[ClassifiedEvent], None]:
    seen_thread_start = False

    def _on_event(event: ClassifiedEvent) -> None:
        nonlocal seen_thread_start
        if event.kind is ClassifiedKind.TOOL_CALL:
            line = describe_activity(event, cwd=cwd)
            if line is not None:
                on_activity(line)
        elif not seen_thread_start and event.event_type == "thread.started":
            seen_thread_start = True
            on_activity("session started")

    return _on_event


def _summarize(reader: StreamReader, exit_code: int) -> str:
    terminal = reader.terminal_event
    if terminal is not None:
        if terminal.event_type == "turn.completed":
            return "turn completed"
        error = terminal.payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return f"turn failed: {message}"
        return "turn failed"
    error = reader.latest_error
    if error is not None:
        message = error.payload.get("message")
        if isinstance(message, str) and message:
            return f"error: {message}"
    return f"exit code {exit_code}"
