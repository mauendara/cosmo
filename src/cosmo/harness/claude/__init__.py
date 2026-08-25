"""Claude Code CLI adapter package (spec 2.3, plan Phase 3).

Split from a single `claude.py` into a package once Phase 3 added the
stream-json reader: `stream.py` sits beside `adapter.py` rather than in core
because the wire format is this harness's own (see `stream.py`'s docstring).
"""

from __future__ import annotations

from cosmo.harness.claude.adapter import BILLING_ENV_VAR, ClaudeCodeAdapter

__all__ = ["BILLING_ENV_VAR", "ClaudeCodeAdapter"]
