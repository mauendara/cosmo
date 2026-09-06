"""Shared, stdlib-only helpers for Cosmo's Codex PreToolUse hooks."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROTECTED_PATTERNS = (
    "src/test/**",
    "e2e/**",
    "**/*.spec.ts",
    "**/*.test.ts",
    "**/*.spec.tsx",
    "**/*.test.tsx",
    "**/*.spec.jsx",
    "**/*.test.jsx",
)
SECRET_PATTERNS = (".env*", "**/.env*", "secrets/**", "**/*.pem", "**/id_rsa*")
PATCH_HEADER = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$")
PATCH_MOVE = re.compile(r"^\*\*\* Move to: (.+)$")


def read_hook_input() -> dict[str, Any]:
    try:
        value = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    raise SystemExit(0)


def allow() -> None:
    raise SystemExit(0)


def project_dir(payload: dict[str, Any]) -> Path:
    return Path(str(payload.get("cwd") or os.getcwd())).resolve()


def relative_path(raw_path: str, root: Path) -> str:
    path = Path(raw_path.strip().strip("\"'"))
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve(strict=False).relative_to(root).as_posix()
    except ValueError:
        return "../" + path.resolve(strict=False).as_posix().lstrip("/")


def matches(path: str, patterns: tuple[str, ...]) -> str | None:
    normalized = path.removeprefix("./")
    for pattern in patterns:
        if fnmatch.fnmatch(normalized, pattern):
            return pattern
    return None


def patch_command(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    return str(tool_input.get("command") or "")


def patch_paths(patch: str, root: Path) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        match = PATCH_HEADER.match(line) or PATCH_MOVE.match(line)
        if match:
            paths.append(relative_path(match.group(1), root))
    return paths


def added_patch_lines(patch: str) -> list[str]:
    return [line[1:] for line in patch.splitlines() if line.startswith("+")]


def shell_command(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    return str(tool_input.get("command") or "")


def shell_tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        # An unparseable shell command is unsafe for policy decisions. Callers
        # can conservatively deny it when the relevant guard is active.
        return []


def shell_has_background_operator(command: str) -> bool:
    quote = ""
    escaped = False
    for index, char in enumerate(command):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if char in {"'", '"'}:
            if not quote:
                quote = char
            elif quote == char:
                quote = ""
            continue
        if char == "&" and not quote:
            before = command[index - 1] if index else ""
            after = command[index + 1] if index + 1 < len(command) else ""
            if before != "&" and after != "&":
                return True
    return False


def shell_is_mutating(command: str) -> bool:
    tokens = shell_tokens(command)
    if not tokens:
        return bool(command)
    mutation_words = {
        "bash",
        "cp",
        "dd",
        "install",
        "mkdir",
        "mv",
        "node",
        "perl",
        "python",
        "python3",
        "rm",
        "rmdir",
        "ruby",
        "sh",
        "tee",
        "touch",
        "truncate",
        "unlink",
    }
    if any(token in {">", ">>"} or ">" in token for token in tokens):
        return True
    if any(Path(token).name in mutation_words for token in tokens):
        return True
    if re.search(
        r"\bgit\s+(?:add|am|apply|checkout|cherry-pick|clean|commit|merge|mv|"
        r"rebase|reset|restore|revert|rm|stash|switch)\b",
        command,
    ):
        return True
    # Real review validation found the old regex treated `sed -n path/skills`
    # as mutating because it paired the `-` from `-n` with the unrelated `i`
    # in `skills`. Inspect option tokens from each sed command segment so only
    # an actual in-place flag (`-i`, `-ni`, `--in-place`) counts. Perl remains
    # conservatively mutating above because arbitrary Perl code can write
    # without an in-place flag.
    for index, token in enumerate(tokens):
        if Path(token).name != "sed":
            continue
        for option in tokens[index + 1 :]:
            if option in {";", "&", "&&", "|", "||"}:
                break
            # Stop at the first script/file operand. Otherwise a later path
            # or newline-separated command can masquerade as an option merely
            # because one of its tokens begins with `-`.
            if not option.startswith("-"):
                break
            if option.startswith("--in-place"):
                return True
            if option.startswith("-") and not option.startswith("--") and "i" in option[1:]:
                return True
    return False


def paths_mentioned(command: str, root: Path) -> list[str]:
    """Return path-like shell tokens without trying to execute shell syntax."""
    result: list[str] = []
    for token in shell_tokens(command):
        candidate = token.strip("=:,")
        if candidate in {".", ".."} or "/" in candidate or candidate.startswith(".env"):
            result.append(relative_path(candidate, root))
    return result


def task_allows_test_edits() -> bool:
    db_path = os.environ.get("COSMO_DB_PATH")
    task_id = os.environ.get("COSMO_TASK_ID")
    if not db_path or not task_id or not os.path.isfile(db_path):
        return False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            row = conn.execute(
                "SELECT allow_test_edits FROM task_queue WHERE task_id = ?", (task_id,)
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return row is not None and bool(row[0])
