# Handoff — continue at Phase 5

You are picking up Cosmo mid-build. Phases 0-4 are complete. Your job is
Phase 5: worktree lifecycle and git operations — `git worktree add`, the
per-worktree `gitleaks` pre-commit hook, retention/teardown policy, and the
merge-conflict recovery ladder. This is also where `sync_harness_assets`
(Phase 4) gets its second real call site.

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth.** v1 and v2 are superseded — read them only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map. Phase 5 is your scope (§3.2, §3.4, §6.1's secret-handling half) |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus decisions and gotchas | Read the "Phase 4 — Complete" section in full before writing code — several of its decisions are load-bearing for Phase 5 |

`v1-*` and `v2-*` in this folder are earlier spec drafts. v3 is a superset of
both. Do not implement from them.

**Do not edit the plan document.** It is the agreed scope. Record what you
build, and any decision you make along the way, in `v3-implementation-state.md`.

## Where things are

```
/home/dev/delta/cosmo/          # working branch: develop
├── docs/                       # the four documents above
├── templates/                  # Phase 4: harness + project templates (source of truth)
│   ├── harness/claude/           # CLAUDE.md, settings.json, hooks/, agents/, skills/
│   └── projects/{_blank,java-spring-react}/docs/
├── src/cosmo/
│   ├── checks.py                 # CheckResult / CheckStatus
│   ├── config/                   # typed model, defaults.toml, three-layer loader
│   ├── doctor.py                  # core preflight checks
│   ├── harness/                  # base ABC (+cwd, +probe), registry, claude/, fake/
│   │   ├── claude/                 # adapter.py (+--setting-sources project) + stream.py
│   │   └── fake/                   # FakeHarnessAdapter -- target this in every new test
│   ├── bootstrap/                 # Phase 4: template discovery, sync, symlinks, cosmo init
│   ├── store/                    # SQLite schema, StoreWriter, reader queries (Phase 1)
│   ├── events/                   # envelope + EventEmitter, transactional sequence (Phase 1)
│   ├── proc/                     # ManagedProcess (+on_stdout_chunk), timers, orphan sweep, reap (Phase 2/3)
│   ├── cli/main.py               # `cosmo` command: config, harness, doctor, queue, events, project, init, templates
│   └── {git,gate,task,run,knowledge}/   # EMPTY — later phases
├── tests/                       # 181 passing
└── check.sh                     # ruff + format + mypy --strict + pytest
```

`src/cosmo/git/` is empty and is exactly where Phase 5's worktree manager
goes.

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # Phase 4 should be committed at HEAD
git branch --show-current   # should say develop
./check.sh                  # must be green before you change anything
cosmo doctor                # core checks + harness checks in two tables
cosmo templates list        # Phase 4's template registry
```

Try `cosmo init` against a real scratch git repo once, by hand, to see the
whole Phase 4 flow before building on top of it:

```bash
D=$(mktemp -d); cd "$D" && git init -q
COSMO_CONFIG=/nonexistent/config.toml XDG_DATA_HOME="$D/.cosmo-data" \
  cosmo init . --project-template java-spring-react
find . -not -path './.git*' | sort
```

**Known, pre-existing environment noise on this host** (not something Phase
5 broke, don't chase it): `cosmo doctor` may show `disk space: FAIL` — this
WSL2 box runs close to the 10 GB floor. Also: this box has no git identity
configured globally (`git commit` fails with "Author identity unknown"
unless a repo/global `user.email`/`user.name` is set) — harmless for
Cosmo's own tests (they don't shell out to `git commit` for real yet), but
you will hit it the moment Phase 5's commit step does. Either set a global
identity for this box or make Cosmo's own worktree commits pass `-c
user.name=... -c user.email=...` explicitly — decide and document which.

## Conventions this codebase follows

- **Python 3.12+, `uv`-managed.** Add dependencies with `uv add`, not by hand.
- **`mypy --strict` passes.** Annotate everything, including test helpers.
- **Comments explain *why*, never *what*.** Existing comments cite the spec
  section that forced the decision. Match that.
- **Config over constants.** Every tunable goes in `config/model.py` and
  `config/defaults.toml`, annotated with its spec section. No magic numbers.
- **Validators catch what would fail silently.** See the existing timeout,
  playwright-tag, and template-hash-exclusion validators/decisions for the
  pattern: reject or exclude at the source what would otherwise misbehave
  or drift silently.
- **Tests isolate from the developer's environment.** Anything touching config
  must set `COSMO_CONFIG` and `XDG_DATA_HOME` to temp paths — see the autouse
  fixture in `tests/test_cli.py`. Anything touching a real git repo should
  build one in `tmp_path`, never touch this repo or a real target repo.
- **Fake the external process, test the mechanics.** `FakeHarnessAdapter`
  (`cosmo.harness.fake`) is the harness test double; `fake_docker.sh`,
  `fake_claude.sh`, `fake_openspec.sh` are the subprocess test doubles
  (`tests/fixtures/`). Phase 5's `git`/`gitleaks` calls are a new case:
  decide whether they need a fake too, or whether real `git` (already a
  dependency, already fast, already offline) is fine to call for real in
  unit tests the way `openspec init` turned out to be (see Phase 4 state doc
  decision — it was probed by hand first, found safe, and used for real
  rather than faked). `gitleaks` itself is a new external binary Cosmo
  hasn't touched yet — check whether it's even on this box before assuming
  it is (`cosmo doctor` doesn't check for it yet; decide if it should).
- **Boundary tests are load-bearing, not optional.** `test_harness_boundary.py`
  keeps harness-specific tokens out of core (`harness/claude/*.py`,
  `harness/registry.py`, and now `bootstrap/symlinks.py` — added in Phase 4
  because per-harness root-link naming is genuinely harness-aware knowledge
  — are the allowed exceptions, plus `config/defaults.toml`).
  `test_store_boundary.py` keeps `connect_writer` from leaking outside
  `store/writer.py` and `store/migrations.py`. Check both before adding any
  module that imports across these boundaries.
- **Run `./check.sh` before committing.** All four must pass.
- **When something fails, check with a real invocation before trusting a
  unit test's green.** This has found a real bug or made a real design
  decision correctly in every phase so far — Phase 2's two worst bugs,
  Phase 3's `rate_limit_event` deviation, and Phase 4's `openspec --tools
  claude` conflict, `--setting-sources project` fix, and `__pycache__`
  hashing bug were all found this way. For Phase 5: actually run `git
  worktree add` against a real repo with real branches, actually force a
  real merge conflict between two worktrees, and actually run whatever
  `gitleaks` invocation you build against a file that should trip it — don't
  only trust the unit tests' green.

## Phase 5 scope

Spec §3.2 (isolation, retention), §3.4 (merge-conflict policy), §6.1's
secret-handling half (the `gitleaks` pre-commit hook and gate-side backstop
— the backstop scan itself is Phase 6's gate, not yours; you own the
per-worktree hook installation).

Summary from the plan:

1. **`git worktree add <work>/<run_id>/<task_id> -b task/<spec-id> develop`.**
   `config.paths.work_dir` (already exists, Phase 0) is the `<work>` root;
   `config.git.base_branch` (already exists) is `develop`. Call
   `sync_harness_assets(worktree_path, harness, emitter=..., run_id=...)`
   **immediately after** worktree creation, before `PROPOSING` starts (spec
   10.5) — this is Phase 4's second call site, and the reason `run_id` is
   already a parameter on `sync_harness_assets`.
2. **Install a `gitleaks` pre-commit hook in each worktree** (spec 6.1).
   Check whether `gitleaks` is installed on this box before assuming a
   binary check belongs in `doctor.py`'s core checks (it's a Cosmo
   dependency like `openspec`/`docker`, not harness-specific — see
   `doctor.py`'s own comment on why those two are core). Decide the hook's
   exact form (a `.git/hooks/pre-commit` script invoking `gitleaks protect
   --staged` or equivalent) and document the choice — spec 6.1 names the
   tool but not the exact invocation, another Open-Item-4-shaped gap.
3. **Teardown policy.** `git worktree remove --force` on `DONE`; **retain**
   on `BLOCKED` for inspection. `worktree_path` already exists as a
   `task_queue` column (Phase 1) — this phase is what actually writes and
   reads it for real.
4. **Startup sweep** pruning worktrees belonging to completed runs. Related
   to but distinct from Phase 2's `find_worktree_holders` (which finds
   *processes* holding a worktree path, not stale worktree directories
   themselves) — don't conflate the two, but do reuse Phase 2's `/proc`-scan
   pattern if it's the right shape for this too.
5. **Commit step**, then merge into `develop` with the §3.4 ladder:
   - Attempt a standard merge.
   - On conflict, attempt **exactly one** automated recovery: rebase the
     task branch onto current `develop`, then re-run the **full validation
     gate** (Phase 6 doesn't exist yet — this call site is a seam for now,
     the same way Phase 3 left worktree lifecycle a seam for you).
   - If the gate passes post-rebase, merge. If the rebase itself conflicts,
     skip straight to `BLOCKED` with `blocked_reason = merge_conflict`,
     worktree and branch **retained**, `task.blocked` at `severity =
     warning`.
   - **The conflict is never handed back to the agent to resolve blind**
     (spec 3.4 step 2) — enforce this structurally (e.g. the merge/rebase
     code path never has a harness adapter in scope at all), not by
     convention or a comment.
   - `merge_conflict` is excluded from the circuit-breaker tally (spec 3.4,
     6.5) — there's no breaker yet (Phase 8), but don't build merge-conflict
     handling in a shape that would need rework once it exists; a
     `blocked_reason` enum value already exists for this (Phase 1).
6. **`master` is never a merge target anywhere in the codebase** — spec 3.2:
   merging `develop` → `master` is manual, developer-performed, explicitly
   out of scope. Add a test that asserts this the same way
   `test_harness_boundary.py` asserts its own invariants — grep for the
   literal string across `src/cosmo/git/` (and anywhere else a merge target
   could be named) and fail if `master` appears anywhere but a comment
   explaining why it's excluded.

### Exit criteria (from the plan)

- A scripted two-task conflict scenario in a fixture repo: rebase recovery
  succeeds in one case, and in the other produces `BLOCKED` with
  `merge_conflict`, retained worktree, and a `warning`-severity
  `task.blocked`.
- `master` is never a merge target anywhere in the codebase — asserted by
  test.

## Things to know before you start

**`config.paths.work_dir` and `config.git.base_branch` already exist**
(Phase 0) — this phase is the first real consumer of either. Check
`cosmo doctor`'s `check_work_dir_filesystem` (Phase 0) still makes sense
once worktrees are actually being created there for real, not just
theorized about.

**`task_queue.worktree_path` and `blocked_reason = 'merge_conflict'` already
exist in the schema** (Phase 1) — this phase writes to a column and uses an
enum value that have been sitting ready since Phase 1. No migration should
be needed for the worktree lifecycle itself; if you find you need one
anyway, that's worth double-checking against the Phase 1 schema before
assuming it's missing.

**`sync_harness_assets`'s `run_id` parameter has no real caller yet outside
tests.** This phase is what actually exercises it with a real run in
progress. If the shape turns out to be wrong once you have a real caller,
that's useful information — record it as a Phase 4 spec deviation retroactively
rather than silently reshaping the function without a note.

**Phase 2's `cancel_and_reap` already does the process/container half of
cleanup on a killed task** (`os.killpg` + `docker rm -f` + worktree-holder
detection). Phase 5's worktree *removal* is a different, later step — a
task can be fully reaped (no live processes, no live containers) and still
have a worktree directory sitting on disk waiting for `DONE`/`BLOCKED` to
decide its fate. Don't duplicate Phase 2's process cleanup; do build on top
of it.

**Nothing before Phase 8 should implement circuit-breaker trip logic or run
scheduling** — unchanged since Phase 3's handoff, still applies. Phase 5 is
about worktrees and merges existing correctly, not about what the run loop
does with the outcome.

## When you finish

1. `./check.sh` green.
2. Update `v3-implementation-state.md`: mark Phase 5 complete, list what
   exists, record every decision made and anything a future session would
   otherwise rediscover. Append any new spec deviation to the cumulative
   table at the bottom.
3. Commit to `develop` with a message explaining *why*, in the style of the
   Phase 0-4 commits.
4. Rewrite this handoff for Phase 6 (the validation gate) — or delete it if
   the next session continues immediately.

Phase 6 is next: the Docker validation gate (build → unit → e2e, serial),
the diff gate (spec 6.1 layer 2 — test-integrity detection), flaky-test
confirm-by-rerun and quarantine handling (spec 6.4), and the
`error_detail`/structured-result construction (spec 9.3) the retry prompt
and `task.validation_result` event both depend on. It's called out in the
plan as the largest phase — build its fixture repo first, before the gate
runner itself.
