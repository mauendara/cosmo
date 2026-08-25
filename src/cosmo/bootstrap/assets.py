"""`sync_harness_assets` (spec 10.5): one function, two call sites -- `cosmo
init` (this phase) and worktree creation (Phase 5, not built yet). Replaces
`.agent/<harness>/` in the target repo wholesale from Cosmo's own canonical
`templates/harness/<harness>/`, so a task never runs against a stale copy of
Cosmo's guardrails merely because `init` ran before the templates changed.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from cosmo.bootstrap.discover import harness_template_dir
from cosmo.bootstrap.hashing import compute_template_version
from cosmo.events import EventEmitter, EventType, Severity


@dataclass(frozen=True, slots=True)
class SyncResult:
    target: Path
    harness: str
    dest: Path
    template_version: str
    event_id: str


def sync_harness_assets(
    target: Path,
    harness: str,
    *,
    emitter: EventEmitter,
    run_id: str | None = None,
    templates_root: Path | None = None,
) -> SyncResult:
    """Replace `target/.agent/<harness>/` wholesale from Cosmo's canonical
    template tree and emit `agent_assets.synced` (spec 9.2).

    `run_id` is None at `cosmo init` time (no run exists yet) and populated
    by Phase 5's worktree-creation call site -- `EventEmitter` already scopes
    `sequence` by `run_id or ""` (Phase 1 decision), so a run-less sync and a
    per-task sync both number correctly without a special case here.
    """
    source = harness_template_dir(harness, root=templates_root)
    dest = target / ".agent" / harness
    # Hash the source, not the copy: spec 9.2 asks for "a hash of the source
    # template tree" -- what Cosmo shipped, not a claim about what copytree
    # actually reproduced byte-for-byte.
    version = compute_template_version(source)

    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # __pycache__/*.pyc excluded for the same reason hashing.py excludes them
    # from template_version: stray bytecode from a hook having been run
    # locally is not part of the template.
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))

    event = emitter.emit(
        event_type=EventType.AGENT_ASSETS_SYNCED,
        severity=Severity.INFO,
        run_id=run_id,
        payload={
            "harness": harness,
            "template_version": version,
            "target_path": str(target),
        },
    )
    return SyncResult(
        target=target,
        harness=harness,
        dest=dest,
        template_version=version,
        event_id=event.event_id,
    )
