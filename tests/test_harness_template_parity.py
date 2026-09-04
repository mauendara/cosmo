"""`templates/harness/ori-claude/` and `templates/harness/claude-openrouter/`
vs `templates/harness/claude/` (v13 plan Phase 4/5; claude-openrouter added
2026-09-04 alongside its adapter).

The template is a deliberate full copy, not a shared/inherited tree (plan
decision 2: "Templates are data, synced wholesale; a self-contained
directory is auditable by reading one place... The accepted cost (hook
scripts existing twice) is bought back by a parity test.") This is that
test: it turns silent drift between the `hooks/` trees into a failed test
instead of a silently-disabled guardrail. Parametrized over every non-native
harness template so a fourth one gets the same coverage for free.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

HARNESS_TEMPLATES_ROOT = Path(__file__).resolve().parents[1] / "templates" / "harness"
CLAUDE_HOOKS = HARNESS_TEMPLATES_ROOT / "claude" / "hooks"
NON_NATIVE_HARNESSES = ["ori-claude", "claude-openrouter"]


def _hook_files(root: Path) -> dict[str, Path]:
    return {
        str(p.relative_to(root)): p
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith((".pyc", ".pyo"))
    }


@pytest.mark.parametrize("harness", NON_NATIVE_HARNESSES)
def test_hooks_directories_have_identical_file_lists(harness: str) -> None:
    claude_files = set(_hook_files(CLAUDE_HOOKS))
    other_files = set(_hook_files(HARNESS_TEMPLATES_ROOT / harness / "hooks"))

    assert claude_files == other_files


@pytest.mark.parametrize("harness", NON_NATIVE_HARNESSES)
def test_every_hook_file_is_byte_identical_between_harnesses(harness: str) -> None:
    claude_files = _hook_files(CLAUDE_HOOKS)
    other_files = _hook_files(HARNESS_TEMPLATES_ROOT / harness / "hooks")

    mismatched = [
        rel
        for rel, other_path in other_files.items()
        if rel in claude_files and other_path.read_bytes() != claude_files[rel].read_bytes()
    ]

    assert mismatched == [], f"hook files drifted from templates/harness/claude/: {mismatched}"


@pytest.mark.parametrize("harness", NON_NATIVE_HARNESSES)
def test_settings_hooks_reference_only_their_own_agent_dir(harness: str) -> None:
    text = (HARNESS_TEMPLATES_ROOT / harness / "settings.json").read_text()

    assert f".agent/{harness}/" in text
    for other in [*NON_NATIVE_HARNESSES, "claude"]:
        if other != harness:
            assert f".agent/{other}/" not in text


@pytest.mark.parametrize("harness", NON_NATIVE_HARNESSES)
def test_settings_declares_no_model_key(harness: str) -> None:
    settings = json.loads((HARNESS_TEMPLATES_ROOT / harness / "settings.json").read_text())

    assert "model" not in settings


@pytest.mark.parametrize("harness", [*NON_NATIVE_HARNESSES, "claude"])
def test_settings_denies_every_one_shot_hazard_tool(harness: str) -> None:
    """`ScheduleWakeup`/`ToolSearch`/`TaskOutput` and (2026-09-04) `Task`
    itself must stay denied on every harness template -- each was added
    after a real session tried to make its one-shot call outlive its own
    turn (see this section's CLAUDE.md, "This call is one-shot"). A future
    template edit that silently drops one of these would reopen a closed
    hazard rather than fixing a new one."""
    settings = json.loads((HARNESS_TEMPLATES_ROOT / harness / "settings.json").read_text())

    deny = settings["permissions"]["deny"]
    for hazard_tool in ("ScheduleWakeup", "ToolSearch", "Task", "TaskOutput"):
        assert hazard_tool in deny, f"{harness}/settings.json no longer denies {hazard_tool!r}"
