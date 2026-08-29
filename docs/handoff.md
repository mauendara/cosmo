# Handoff — v0.1.1, three real bugs from v0.1.0's first real usage

This document was compressed for v0.1.0 forward: ~10 sessions' worth of
session-by-session narrative (what changed, what was found, how it was
fixed) has been cut. That history isn't lost — it's in `git log` (every
commit message explains its own *why*) and in
[v3-implementation-state.md](v3-implementation-state.md)'s cumulative
deviations table (the complete bug/fix log, entries 1-82). This file now
only carries what a session needs to *orient itself* before doing new work,
not a record of how we got here.

## Where things stand

- **All 11 build phases done**, plus the v4 raw-spec-workflow feature, the
  v5 improvements plan (crash/resume, Telegram notify, `--follow`,
  live-terminal observability, quota-bypass), and v7 items 1-3. v6
  (template-aware gate/failure-classifier) is **deliberately not started**
  — see its own plan doc; it needs a second real stack to prove the
  abstraction, and the user is doing that testing separately before it gets
  picked up again.
- **562 tests passing, 9 skipped, `./check.sh` green** as of the last code
  change. Every fix in the deviations table has a regression test.
- **v0.1.1 is a patch release**: v0.1.0 got its first real usage (a real
  `cosmo run` against a real target repo, not this repo's own test suite)
  and surfaced three real bugs, all fixed and covered by a regression test
  — deviations 80-82 in `v3-implementation-state.md`: `.gitignore`'s
  blanket `data/` rule silently dropping `gate/data/*.yml` from every built
  wheel, `notify.watch` forwarding `task.heartbeat` spam to Telegram at
  `min_severity="info"`, and `docs/specs/` content that `spec add`/`spec
  queue` write into `repo_path` never getting committed, which blocked
  every later merge. A real installed-tool bug (a non-editable `uv tool
  install` breaking `templates_root()`) was also root-caused and fixed —
  see the environment-gotchas section below, not a numbered deviation since
  no code changed. `assets/cosmo-demo.gif` was also added to both READMEs.
- **Public docs shipped**: `README.md`, `user-docs/` (EN+ES, Diátaxis
  layout), `FAQ.md`, `TROUBLESHOOTING.md`, `CONTRIBUTING.md`, `SECURITY.md`.
  Everything in them is grounded in the code (real CLI `--help` output,
  real config keys, real event payloads), not copied from the internal
  specs — see [v10-user-docs-discrepancies.md](v10-user-docs-discrepancies.md)
  for the handful of places the code is narrower than the original brief.
- **Repo audited twice for open-source release** (secrets, personal data,
  AI-attribution, licensing, hygiene) — clean both times, most recently
  right before this compression. `LICENSE` is Apache-2.0; `pyproject.toml`,
  `README.md`, `CONTRIBUTING.md`, `SECURITY.md` all reference it.
  `AGENTS.md` points at `CONTRIBUTING.md`'s "Commits and AI attribution"
  section as the one canonical copy of the no-AI-trailer policy —
  deliberate pointer pattern, don't duplicate the text.
- **AI-attribution trailers were stripped from the entire git history** via
  `git filter-repo` — every commit hash from before 2026-08-28 changed as a
  result. Don't expect old hashes quoted anywhere to `git show`.
- **Branch topology**: `private` (this branch, as of this session) is the
  maintainer's day-to-day branch — CONTRIBUTING.md's branching model routes
  `private` → `develop` → the public remote, never `private` straight to
  public. `develop` is the PR-integration/release branch; `private` is
  currently 6 commits ahead of it (this session's v0.1.1 patch work, not
  yet merged). `master` is a stale 1-commit skeleton, far behind — not part
  of any push. `webapp` (a separate in-progress monitoring-UI feature) is
  missing `LICENSE` and not release-ready — don't push it without doing
  that work first. `.githooks/pre-push` (active via `core.hooksPath`)
  refuses to push a branch literally named `private` to whatever `origin`
  resolves to; it does **not** guard against pushing `master`/`webapp` by
  habit, so name the branch explicitly when pushing.
- **Remotes**: `private-origin` (`git@github.com:deltam-dev/private-cosmo.git`)
  is configured and is where `private` gets pushed for backup — it's also 6
  commits behind local `private` right now (this session's work hasn't been
  pushed there yet). No public `origin` remote is configured yet; no `gh`
  CLI on this host. **v0.1.0 was never actually pushed publicly** — v0.1.1
  (this patch) is what will actually make the first public push once
  `private` merges into `develop` and the maintainer sets up the public
  remote themselves.

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth** for the original 0-10 plan. v1/v2 are superseded — read only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map for what's built. **Do not edit** — record decisions in `v3-implementation-state.md` instead |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus the cumulative deviations table (1-82) and implementation-time decisions | Read the most recent deviation entries before doing anything non-trivial |
| [v4-changes-to-workflow-plan.md](v4-changes-to-workflow-plan.md) | The raw-spec-workflow feature design | Implemented — see its own Status line |
| [v5-improvements-plan.md](v5-improvements-plan.md) | Crash/pause resume, Telegram notifications, `--follow`, live-terminal observability, quota-bypass, harness failure-pattern research | Implemented — see its own Status line |
| [v6-project-template-aware-stuff-plan.md](v6-project-template-aware-stuff-plan.md) | Making the gate/failure-classifier project-template-aware, for stacks beyond Java+Spring/Vite+React | **Not started — design record only.** Needs a real second stack before it's buildable; don't start opportunistically |
| [v7-complete-queue-done-fixes-plan.md](v7-complete-queue-done-fixes-plan.md) | Closing the "queue_empty looks like done" gap | Items 1-3 done. Only item 4 (a spec-authoring question, not code) remains open |
| [v8-validations-for-later.md](v8-validations-for-later.md) | Real-invocation validations still owed | **Tracking document, not a plan.** Update an entry in place when it gets a real run |
| [v9-out-of-scope-desirables.md](v9-out-of-scope-desirables.md) | Everything declared out of scope, deferred, or still an open design decision | **Tracking document, not a plan.** Read before assuming a gap is an oversight |
| [v10-user-docs-discrepancies.md](v10-user-docs-discrepancies.md) | Where the public-docs brief described Cosmo differently from what the code does | **Tracking document, not a plan.** Read before touching `task.guardrail_tripped`, the diff gate's `test_path_modified` rule, or assuming gate stage commands are configurable |

Internal `vN` documents above are **not** the user-facing ones. Public docs
live in `README.md`, `user-docs/`, and the four root docs — written for a
developer who has never seen the project. Keep the two sets separate:
internal design deliberation must not leak into user docs, and a user-doc
change that contradicts the code is a bug. `v1-*`/`v2-*` and
`simple-template-handoff.md`/`old-agents-skills/` are historical, fully
superseded/consumed.

## Environment gotchas that will still bite

- **WSL2 `cosmo doctor` may show `disk space: FAIL`** — this box's `/tmp` is
  a small tmpfs; the real filesystem has hundreds of GB free. Known noise,
  not a regression.
- **This shell may have `XDG_DATA_HOME`/`COSMO_CONFIG` pointed at a sandbox**
  (e.g. `/tmp/cosmo-test/data`). To inspect/drive the *real* store, unset
  both explicitly (`env -u XDG_DATA_HOME -u COSMO_CONFIG cosmo ...`) rather
  than assuming the ambient env is clean. `uv tool install` respects
  `XDG_DATA_HOME` too — a sandboxed env silently installs the `cosmo` tool
  to the wrong prefix with no error; check the installed binary's mtime.
- **The installed `cosmo` tool must be an editable install** (`uv tool
  install --editable .`, per `README.md`/`CONTRIBUTING.md`) — `templates/`
  lives at the repo root, not inside the package, and `bootstrap.discover.
  templates_root()` finds it by walking up from `cosmo.__file__`, which only
  lands back in the real checkout when the install is editable. Found for
  real 2026-08-29: at some point (likely release-prep packaging/testing on
  2026-08-28) the real installed tool had silently become a plain, non-
  editable `uv tool install .` copy into `site-packages` — invisible for a
  while because every task in flight was reusing an already-created
  worktree (`create_worktree`/`sync_harness_assets` only runs for a *new*
  worktree), until a crash-recovery requeue cleared a task's
  `worktree_path` and the next `cosmo run` needed a fresh one, hitting
  `TemplatesRootNotFoundError` immediately. Fix is `uv tool install
  --editable --force --reinstall .`; verify with `cosmo templates list`
  (should list real harnesses/project templates, not error) rather than
  trusting `cosmo doctor` alone (it doesn't check this).
- **`npm install` can hang indefinitely** if a previous run was killed
  mid-install — fix is a verified-clean `rm -rf node_modules
  package-lock.json` first, not waiting longer.
- **Systemd (`systemctl --user`) units exist for real** on this host:
  `cosmo-run.service`, `cosmo-notify.service` (Telegram creds live in
  `~/.config/cosmo/config.toml`, `chmod 600`, never committed).
  `acquire_run_lock` is one `cosmo run` at a time **per `data_dir`, not per
  project** — a manual `cosmo run` and a service auto-start against
  different projects can collide with `RunLockHeldError`.
- **Manually seeding/removing `task_queue` rows** against the real store
  requires respecting real foreign-key dependents (`task_failures`,
  `task_transitions`, `events`, `task_progress`, `task_heartbeat`,
  `task_cost`) and committing once at the end of one script — a raw
  `sqlite3 DELETE` outside a full committed transaction rolls back silently
  on any mid-script `IntegrityError`. Prefer the CLI; there is currently no
  `cosmo queue remove <task_id>`, so direct DB access is a deliberate,
  flagged action, not a routine shortcut.

## Conventions this codebase follows

- **Python 3.12+, `uv`-managed.** Add dependencies with `uv add`, not by hand.
- **`mypy --strict` passes.** Annotate everything, including test helpers.
- **Comments explain *why*, never *what*.** Existing comments cite the spec
  section that forced the decision. Match that.
- **Config over constants.** Every tunable goes in `config/model.py` and
  `config/defaults.toml`, annotated with its spec section. No magic numbers.
- **Tests isolate from the developer's environment.** Anything touching
  config must set `COSMO_CONFIG`/`XDG_DATA_HOME` to temp paths. Anything
  touching a real git repo builds one in `tmp_path`, never this repo or a
  real target repo.
- **Fake the external process, test the mechanics — except where a real
  invocation already proved something out.** `FakeHarnessAdapter` and
  `FakeGate` are the two test doubles to target directly. Real-process/
  real-Docker/real-`openspec` tests exist, mostly gated behind
  `which openspec`/`COSMO_GATE_DOCKER_E2E=1` skipif guards.
- **Boundary tests are load-bearing, not optional.** `test_harness_boundary.py`,
  `test_store_boundary.py`, `test_git_boundary.py`, `test_gate_boundary.py`.
- **Run `./check.sh` before committing.** All four checks must pass.
- **When something fails, check with a real invocation before trusting a
  unit test's green.** Most deviations in the cumulative table were found
  this way, including some a real attempt at validating something *else*
  surfaced by accident.
- **Scratch work for a real invocation** goes in a scratch directory, never
  a real target repo — and gets cleaned up after (worktree/branch removed,
  seeded DB rows deleted respecting FK order, scratch repo deleted).
  Verify the real queue/repo are untouched before reporting done.

## When you finish

1. `./check.sh` green (if any code changed at all).
2. Record any new deviation in `v3-implementation-state.md`'s cumulative
   table (next number is **83**).
3. Commit to the current branch (`private`, per CONTRIBUTING.md's branching
   model — day-to-day work never targets `develop` directly) with a message
   explaining *why*, in the style of the existing commit history.
4. Keep [v8-validations-for-later.md](v8-validations-for-later.md),
   [v9-out-of-scope-desirables.md](v9-out-of-scope-desirables.md), and
   [v10-user-docs-discrepancies.md](v10-user-docs-discrepancies.md) current
   in place rather than letting this material re-accumulate directly in
   this handoff.
5. **If you changed behavior, check whether the public docs still describe
   it correctly.** A new CLI flag needs a row in
   `user-docs/reference/cli.md`; a new config key needs one in
   `config-schema.md` (plus a default, and a validator if a bad value is
   dangerous); a new event needs a payload table in `event-schema.md`. That
   checklist is also written into `CONTRIBUTING.md` for outside
   contributors.
6. If one of v10's discrepancies gets resolved, update its entry there
   *and* the user-facing pages it names.

## Where things are

```
/home/dev/delta/cosmo/          # working branch: private (day-to-day; see
                                 # "Branch topology" above for private -> develop)
├── .githooks/pre-push          # refuses to push `private` to the resolved
│                                  origin URL; active via core.hooksPath
├── LICENSE                     # Apache-2.0
├── AGENTS.md                   # pointer to CONTRIBUTING.md's AI-attribution section
├── README.md, FAQ.md, TROUBLESHOOTING.md, CONTRIBUTING.md, SECURITY.md
├── user-docs/                  # EN + ES, Diátaxis layout
├── docs/
│   ├── handoff.md               # this file
│   ├── v1-*, v2-*                   # superseded spec drafts
│   ├── v3-*                         # spec, plan, and cumulative implementation state
│   ├── v4- through v10-*            # feature plans and tracking docs, see table above
│   └── ignored/prompts/             # gitignored — one-off task prompts, never published
├── deploy/                     # systemd unit files
├── templates/                  # harness + project templates
├── src/cosmo/                  # ground truth — read here first, always
├── tests/
└── check.sh
```

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # note: git filter-repo rewrote every commit hash
                             # pre-2026-08-28 -- older references won't `git show`
git branch --show-current   # should say private (day-to-day work branch)
./check.sh                  # must be green before you change anything
cosmo doctor                # core checks + harness checks in two tables
```
