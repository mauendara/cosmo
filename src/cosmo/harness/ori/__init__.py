"""Ori-routed Claude Code adapter package (spec 2, v13 plan).

`ori claude` execs the real `claude` binary in place, routed through
OpenRouter -- see `adapter.py`'s module docstring for the argv/env facts
this rests on (v12's real-invocation findings).
"""

from __future__ import annotations

from cosmo.harness.ori.adapter import OriClaudeAdapter

__all__ = ["OriClaudeAdapter"]
