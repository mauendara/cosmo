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
from cosmo.bootstrap.git_identity import GitIdentity, read_configured_identity, set_local_identity
from cosmo.bootstrap.hashing import compute_template_version
from cosmo.bootstrap.init import InitResult, NotAGitRepoError, run_init
from cosmo.bootstrap.openspec import (
    OpenSpecInitError,
    OpenSpecResult,
    archive_change,
    ensure_openspec_initialized,
)
from cosmo.bootstrap.symlinks import SymlinkResult, create_root_symlinks

__all__ = [
    "DocsCopyResult",
    "GitIdentity",
    "InitResult",
    "NotAGitRepoError",
    "OpenSpecInitError",
    "OpenSpecResult",
    "SymlinkResult",
    "SyncResult",
    "TemplatesListing",
    "TemplatesRootNotFoundError",
    "archive_change",
    "compute_template_version",
    "copy_project_docs",
    "create_root_symlinks",
    "ensure_openspec_initialized",
    "harness_template_dir",
    "list_templates",
    "project_template_dir",
    "read_configured_identity",
    "run_init",
    "set_local_identity",
    "sync_harness_assets",
    "templates_root",
]
