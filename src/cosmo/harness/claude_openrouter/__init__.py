"""Direct Claude Code + OpenRouter adapter package (found by hand, 2026-09-04).

No `ori` in the loop -- see `adapter.py`'s module docstring for why this
module exists alongside `harness.ori.adapter.OriClaudeAdapter` rather than
replacing it.
"""

from __future__ import annotations

from cosmo.harness.claude_openrouter.adapter import ClaudeOpenRouterAdapter

__all__ = ["ClaudeOpenRouterAdapter"]
