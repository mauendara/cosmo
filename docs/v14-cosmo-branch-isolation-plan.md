# v14: base-branch isolation mode ("cosmo branch")

## Status: design, not started

Nothing in this doc is implemented yet. Follows directly from `v13`
(ori-claude harness, done) as the next requested feature. Grounded by
reading the real current code, not assumed:

- `git.base_branch` is a single **global** config value (`GitConfig.
  base_branch`, `config/defaults.toml`), overridable per-invocation with
  `--base-branch` at every call site (`init`, `run`, `spec add`, `spec
  queue`, `validate`). It is never persisted per-project today, even when
  the `-i` wizard prompts for it — that value is used once, for that one
  `run_init` call, and never written back anywhere.
- The target repo directory (`--repo`/`target_path`) *is* Cosmo's own
  dedicated checkout, not a scratch clone. `git/merge.py` documents that it
  must stay checked out on `base_branch` at all times, because every task
  worktree is `git worktree add -b <task-branch> <base_branch>` off of it,
  and the merge ladder operates directly inside it.
- `cosmo init` never commits anything itself. `openspec/`, `docs/`,
  `.agent/<harness>/`, and the root symlinks land as plain working-tree
  changes; `bootstrap.git_branch.commit_bootstrap_output` commits them at
  the very end of `cli.main.init`, onto **whatever branch is currently
  checked out** in the target repo at that point — skipped only when the
  git-branch step returned `SKIPPED_DIRTY`. This is the exact mechanism
  that pollutes a real `develop`/`main` branch with Cosmo scaffolding
  today, and it's also the reason this feature needs almost no changes to
  that commit step: if the target repo is checked out on a new `cosmo`
  branch by the time `commit_bootstrap_output` runs, the scaffolding commit
  lands there for free.

## What this feature adds

A second base-branch mode, chosen per-project at `cosmo init` (flag or `-i`
wizard):

1. **`direct`** (today's only behavior, stays the default): Cosmo operates
   directly on the configured `base_branch` (e.g. `develop`). Unchanged.
2. **`cosmo_branch`** (new): at init time, Cosmo creates a new branch (e.g.
   `cosmo`, name configurable) off the configured `base_branch`, and from
   then on treats *that* branch as the effective base branch for
   everything — worktree creation, the diff gate's `base_branch...
   task_branch` diff, and where merges land. The real `base_branch` is
   left exactly as it was: no scaffolding commits, no task merges, nothing
   Cosmo does touches it again after the initial fork point.

Uncommitted changes already present on `base_branch` at init time are
stashed (`git stash push -u`, message identifies it as Cosmo's) before the
fork, and the stash is **left in the stash list** rather than auto-popped
anywhere — reported to the user with the exact recovery command. This was
chosen over auto-popping the stash back onto `base_branch` (which would
require checking `target` back out on `base_branch` mid-init, popping, then
re-checking-out `cosmo`) because it never puts `target`'s working tree in a
dirty intermediate state and has one failure mode instead of three.

Keeping an existing `cosmo` branch in sync with upstream `base_branch`
commits made after the fork (e.g. by teammates) is **out of scope for v1**
— explicitly a manual `git merge`/`git rebase` the user runs themselves.
Auto-sync is a real follow-up (v15?) once this ships and the manual
workflow's pain points are known, not before.

## Config surface

### Per-project persistence (not global config)

`base_branch` today is global-only, which is already a known limitation —
one value for every project Cosmo manages. `harness` and `project_template`
don't have this problem: they're stored per-project in the `projects`
table (`store/migrations.py`, `find_project_by_path`). This feature follows
that existing precedent instead of repeating the global-config mistake:
mode and branch name are stored on the `projects` row, not in
`config.toml`.

**New migration (`Migration(11, ...)`)** adds three columns to `projects`:

```sql
ALTER TABLE projects ADD COLUMN base_branch_mode TEXT NOT NULL DEFAULT 'direct'
    CHECK (base_branch_mode IN ('direct', 'cosmo_branch'));
ALTER TABLE projects ADD COLUMN real_base_branch TEXT;
ALTER TABLE projects ADD COLUMN cosmo_branch_name TEXT;
```

- `real_base_branch`: the configured upstream branch this project forks
  from (e.g. `develop`) — recorded at register time so later commands don't
  need to re-derive it from global config, which may since have changed.
  `NULL` for rows migrated forward from before this feature existed
  (backward-compat fallback: treat `NULL` as "use `cfg.git.base_branch`",
  matching current behavior exactly for already-registered projects).
- `cosmo_branch_name`: only meaningful (non-NULL) when `base_branch_mode =
  'cosmo_branch'`.

`ProjectRow` (`store/reader.py`) gains the three matching fields.
`StoreWriter.register_project` gains `base_branch_mode`, `real_base_branch`,
`cosmo_branch_name` keyword params (all defaulted so every existing call
site — including every test that constructs a project row — keeps
compiling unchanged).

### Resolution helper

`cli.main._resolve_project_repo` already loads the `ProjectRow` for every
command that operates against a target repo, and is the existing precedent
for "project registration is one resolution tier" (it already does this
for harness). Add a sibling:

```python
def _resolve_base_branch(project: ProjectRow, base_branch_flag: str | None, cfg: CosmoConfig) -> str:
    if base_branch_flag is not None:
        return base_branch_flag
    if project.base_branch_mode == "cosmo_branch":
        assert project.cosmo_branch_name is not None
        return project.cosmo_branch_name
    return project.real_base_branch or cfg.git.base_branch
```

Replace every existing `resolved_base = base_branch if base_branch is not
None else cfg.git.base_branch` call site in `cli/main.py` (there are at
least three: `run_cmd`/`_run_queue_cmd`, `spec add`, `validate_cmd`) with a
call to this helper, threading the already-resolved `ProjectRow` through.
`--base-branch` stays a full escape hatch at every one of those sites,
unchanged in precedence.

## `cosmo init` orchestration changes

### `bootstrap/git_branch.py`

New functions, alongside the existing `is_git_repo`/`branch_exists`/
`current_branch`/`working_tree_is_clean`/`create_and_checkout_branch`:

- `stash_all(target: Path, message: str) -> bool` — `git stash push -u -m
  <message>` if `working_tree_is_clean(target)` is `False`; returns whether
  anything was actually stashed (no-op, returns `False`, on an already-clean
  tree).
- `create_branch_from(target: Path, new_branch: str, from_branch: str) ->
  None` — `git checkout -b <new_branch> <from_branch>`.

No changes to any existing function in this file — `direct` mode's path
through `run_init` is untouched byte-for-byte.

### `bootstrap/init.py` (`run_init`)

New params: `base_branch_mode: Literal["direct", "cosmo_branch"] =
"direct"`, `cosmo_branch_name: str | None = None`.

Step 1 branches on mode:

- `direct`: exactly today's logic, unchanged.
- `cosmo_branch`:
  1. `repo_freshly_initialized` / `init_repo` unchanged.
  2. `stashed = stash_all(resolved, "cosmo: pre-cosmo-branch stash")` —
     always attempted, regardless of whether `base_branch` already exists,
     so a dirty tree is never a blocker in this mode (the one case
     `direct` mode gives up on via `SKIPPED_DIRTY`).
  3. Ensure `real_base_branch` exists: if it already does (`branch_exists`
     or `current_branch(resolved) == real_base_branch`), leave it as is;
     otherwise `create_and_checkout_branch(resolved, real_base_branch)`
     from current (now-clean, post-stash) `HEAD`.
  4. If `cosmo_branch_name` already exists as a ref (idempotent re-run of
     `cosmo init`), just check it out. Otherwise
     `create_branch_from(resolved, cosmo_branch_name, real_base_branch)`.
  5. `target` now ends up checked out on `cosmo_branch_name` either way —
     this is what makes step 3/4/7 (docs, `.agent/`, symlinks) and the
     final `commit_bootstrap_output` land on the cosmo branch with zero
     changes needed downstream.

`GitBranchOutcome` gains mode-aware variants surfaced back to
`InitResult.git_branch` (exact member names are an implementation detail,
but semantically: fresh-repo-and-cosmo-branch-created,
cosmo-branch-created-from-existing-base, already-on-cosmo-branch, each
optionally combined with "...-after-stash"). None of these should map to
`SKIPPED_DIRTY` — that outcome becomes unreachable in `cosmo_branch` mode
by construction, since dirtiness is always resolved via stash first. This
matters because `cli.main.init`'s `commit_bootstrap_output` call is gated
on `git_branch != SKIPPED_DIRTY`.

`cli.main.init`'s `_GIT_BRANCH_MESSAGES` table gains entries for the new
outcomes, including printing the stash message and recovery command
(`git checkout <real_base_branch> && git stash pop`) whenever a stash
outcome fires.

`register_project` (called from `run_init`'s step 6) passes through
`base_branch_mode`, the resolved `real_base_branch`, and
`cosmo_branch_name` so the project row reflects reality from the start —
`already_registered` re-runs stay exactly as idempotent as today (no
attempt to reconcile/update an existing row's mode; changing a project's
mode after the fact is out of scope here, same as changing its harness is
today).

## `cosmo init -i` wizard changes (`cli/init_wizard.py`)

New prompt, inserted right after the existing `base_branch` prompt:

```
Base branch strategy:
  1) direct   - operate on <base_branch> itself
  2) cosmo_branch - create an isolated branch from <base_branch> so
     templates/harness files never touch it
```

mirroring the existing `_prompt_from_list` pattern already used for
harness/project template. If `cosmo_branch` is chosen, a second prompt for
the branch name, default `"cosmo"`. Both new fields land on
`WizardChoices` (`base_branch_mode: str`, `cosmo_branch_name: str | None`)
and flow into the `run_init` call in `cli.main.init` exactly like
`base_branch` already does — no config-file writes (unlike the model-
overrides prompt, which explicitly writes to global config): this data is
per-project and belongs on the `projects` row via `register_project`, not
in `config.toml`.

Flag-driven/non-interactive `cosmo init` gets matching new flags
(`--base-branch-mode`, `--cosmo-branch-name`) so CI/scripted use isn't
wizard-only, following the same "flag > wizard prompt > default" precedent
`-i` already establishes for every other field.

## Explicit non-goals for v1

- Auto-syncing an existing `cosmo` branch with upstream `base_branch`
  commits (manual for now — see above).
- Any command to change an already-registered project's mode/branch name
  after the fact (`cosmo project update` doesn't exist yet at all; adding
  it is a separate feature).
- Auto-popping the stash back onto `real_base_branch` (left stashed,
  reported explicitly instead).
- Any change to `direct` mode's existing behavior, tests, or outcomes.

## Testing

- `bootstrap/git_branch.py`: unit tests for `stash_all` (clean tree
  no-ops; dirty tree stashes and returns `True`; stash message contains
  the real base branch name) and `create_branch_from`.
- `bootstrap/init.py`: extend `tests/test_bootstrap_init.py` with
  `cosmo_branch`-mode cases mirroring the existing `direct`-mode matrix —
  fresh repo, existing clean base branch, existing dirty base branch
  (must NOT produce `SKIPPED_DIRTY`), idempotent re-run with the cosmo
  branch already present.
- `store/migrations.py`: extend `tests/test_store_migrations.py` with
  migration 11's forward-apply + `PRAGMA foreign_key_check` (existing
  pattern every migration test already follows), plus a
  backward-compatibility case: an existing pre-migration-11 project row
  reads back with `base_branch_mode == "direct"` and `real_base_branch is
  None`, and `_resolve_base_branch` still falls back to `cfg.git.
  base_branch` for it.
- `cli/main.py`: extend the existing `_resolve_project_repo`-adjacent CLI
  tests to cover a `cosmo_branch`-mode project resolving to the cosmo
  branch name (not `real_base_branch`) across `run`, `spec add`, and
  `validate`, with `--base-branch` still overriding it.
- End-to-end: one real-git fixture test (matching the style of
  `tests/test_git_worktree.py`/`tests/test_git_merge.py`) that runs a full
  `cosmo_branch`-mode `init` against a real temp git repo with an
  uncommitted file present, and asserts: the uncommitted file is gone from
  `git status` on the `cosmo` branch, present again after `git checkout
  <real_base_branch> && git stash pop`, and the scaffolding commit
  (`commit_bootstrap_output`) exists on `cosmo` but `real_base_branch`'s
  tip is completely unchanged from before `init` ran.
