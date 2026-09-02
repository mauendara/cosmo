"""`templates/harness/ori-claude/` vs `templates/harness/claude/` (v13 plan
Phase 4/5).

The template is a deliberate full copy, not a shared/inherited tree (plan
decision 2: "Templates are data, synced wholesale; a self-contained
directory is auditable by reading one place... The accepted cost (hook
scripts existing twice) is bought back by a parity test.") This is that
test: it turns silent drift between the two `hooks/` trees into a failed
test instead of a silently-disabled guardrail.
"""

from __future__ import annotations

import json
from pathlib import Path

CLAUDE_HOOKS = Path(__file__).resolve().parents[1] / "templates" / "harness" / "claude" / "hooks"
ORI_CLAUDE_HOOKS = (
    Path(__file__).resolve().parents[1] / "templates" / "harness" / "ori-claude" / "hooks"
)
ORI_CLAUDE_SETTINGS = (
    Path(__file__).resolve().parents[1] / "templates" / "harness" / "ori-claude" / "settings.json"
)


def _hook_files(root: Path) -> dict[str, Path]:
    return {
        str(p.relative_to(root)): p
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith((".pyc", ".pyo"))
    }


def test_hooks_directories_have_identical_file_lists() -> None:
    claude_files = set(_hook_files(CLAUDE_HOOKS))
    ori_claude_files = set(_hook_files(ORI_CLAUDE_HOOKS))

    assert claude_files == ori_claude_files


def test_every_hook_file_is_byte_identical_between_harnesses() -> None:
    claude_files = _hook_files(CLAUDE_HOOKS)
    ori_claude_files = _hook_files(ORI_CLAUDE_HOOKS)

    mismatched = [
        rel
        for rel, ori_path in ori_claude_files.items()
        if rel in claude_files and ori_path.read_bytes() != claude_files[rel].read_bytes()
    ]

    assert mismatched == [], f"hook files drifted from templates/harness/claude/: {mismatched}"


def test_ori_claude_settings_hooks_reference_only_its_own_agent_dir() -> None:
    text = ORI_CLAUDE_SETTINGS.read_text()

    assert ".agent/ori-claude/" in text
    assert ".agent/claude/" not in text


def test_ori_claude_settings_declares_no_model_key() -> None:
    settings = json.loads(ORI_CLAUDE_SETTINGS.read_text())

    assert "model" not in settings
