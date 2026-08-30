"""Spec 11 knowledge-file management: the `COMMITTING` line-cap guardrail
and the structured `decisions-log.md` entry. See `caps.py`/`decisions_log.py`
module docstrings.
"""

from __future__ import annotations

from cosmo.knowledge.caps import docs_md_files, files_over_cap
from cosmo.knowledge.decisions_log import append_decision_entry

__all__ = ["docs_md_files", "files_over_cap", "append_decision_entry"]
