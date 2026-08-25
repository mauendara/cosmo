"""Spec 9.3 `error_detail` construction: actionable content for the retry
prompt, never a full raw log dump. Model-consumable, not archival -- every
builder here truncates to `max_chars` rather than trusting callers to.
"""

from __future__ import annotations

from cosmo.gate.types import DiffGateResult, StageResult

_TRUNCATED_SUFFIX = "\n... (truncated)"


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    keep = max(0, max_chars - len(_TRUNCATED_SUFFIX))
    return text[:keep] + _TRUNCATED_SUFFIX


def build_stage_error_detail(stage: StageResult, *, max_chars: int) -> str:
    if stage.failing_tests:
        lines = []
        for ft in stage.failing_tests:
            entry = f"{ft.test_id}"
            if ft.assertion:
                entry += f": {ft.assertion}"
            if ft.stack_excerpt:
                entry += f"\n{ft.stack_excerpt}"
            lines.append(entry)
        body = "\n\n".join(lines)
    elif stage.error_detail:
        body = stage.error_detail
    else:
        body = stage.error_summary or "no further detail captured"

    if stage.artifact_paths:
        paths = "\n".join(f"  - {p}" for p in stage.artifact_paths)
        body += f"\n\nartifacts (path only):\n{paths}"

    return _truncate(body, max_chars)


def build_diff_gate_error_detail(diff_gate: DiffGateResult, *, max_chars: int) -> str:
    lines = [f"{v.kind}: {v.detail}" for v in diff_gate.violations]
    return _truncate("\n".join(lines), max_chars)
