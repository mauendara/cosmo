"""Project bootstrap & the harness-facing template system (spec 10).

`templates/` itself lives at the repo root, alongside `src/` -- this package
is the code that discovers it, syncs it into a target repo's `.agent/`, links
it at the target repo's root, seeds `docs/`, and drives `cosmo init` end to
end.
"""

from __future__ import annotations

from cosmo.bootstrap.assets import SyncResult, sync_harness_assets
from cosmo.bootstrap.discover import (
    TemplatesListing,
    TemplatesRootNotFoundError,
    harness_template_dir,
    list_templates,
    project_template_dir,
    templates_root,
)
from cosmo.bootstrap.docs import DocsCopyResult, copy_project_docs
from cosmo.bootstrap.hashing import compute_template_version
from cosmo.bootstrap.init import InitResult, NotAGitRepoError, run_init
from cosmo.bootstrap.openspec import OpenSpecInitError, OpenSpecResult, ensure_openspec_initialized
from cosmo.bootstrap.symlinks import SymlinkResult, create_root_symlinks

__all__ = [
    "DocsCopyResult",
    "InitResult",
    "NotAGitRepoError",
    "OpenSpecInitError",
    "OpenSpecResult",
    "SymlinkResult",
    "SyncResult",
    "TemplatesListing",
    "TemplatesRootNotFoundError",
    "compute_template_version",
    "copy_project_docs",
    "create_root_symlinks",
    "ensure_openspec_initialized",
    "harness_template_dir",
    "list_templates",
    "project_template_dir",
    "run_init",
    "sync_harness_assets",
    "templates_root",
]
