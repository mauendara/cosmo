# Handoff — continue at Phase 6

You are picking up Cosmo mid-build. Phases 0-5 are complete. Your job is
Phase 6: the Docker validation gate — the largest phase and the correctness
core. It runs build → unit → e2e serially inside Docker and bypasses the LLM
harness entirely (spec 2.2's `validate`).

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth.** v1 and v2 are superseded — read them only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map. Phase 6 is your scope (§1.1, §1.2, §1.3, §6.1 layer 2, §6.4, §9.3) |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus decisions and gotchas | Read the "Phase 5 — Complete" section in full before writing code — several of its decisions are load-bearing for Phase 6 |

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
│   ├── doctor.py                  # core preflight checks (now includes gitleaks)
│   ├── harness/                  # base ABC (+cwd, +probe), registry, claude/, fake/
│   │   ├── claude/                 # adapter.py (+--setting-sources project) + stream.py
│   │   └── fake/                   # FakeHarnessAdapter -- target this in every new test
│   ├── bootstrap/                 # Phase 4: template discovery, sync, symlinks, cosmo init
│   ├── git/                       # Phase 5: worktree lifecycle, gitleaks hook, merge ladder
│   │   ├── worktree.py             # create_worktree/remove_worktree/sweep_stale_worktrees
│   │   ├── secrets.py              # install_gitleaks_pre_commit_hook
│   │   └── merge.py                # attempt_merge_ladder/merge_task -- GateRerun is YOUR seam
│   ├── store/                    # SQLite schema, StoreWriter, reader queries (Phase 1)
│   ├── events/                   # envelope + EventEmitter, transactional sequence (Phase 1)
│   ├── proc/                     # ManagedProcess (+on_stdout_chunk), timers, orphan sweep, reap (Phase 2/3)
│   ├── cli/main.py               # `cosmo` command: config, harness, doctor, queue, events, project, init, templates
│   └── {gate,task,run,knowledge}/   # EMPTY — later phases (gate is yours)
├── tests/                       # 201 passing
└── check.sh                     # ruff + format + mypy --strict + pytest
```

`src/cosmo/gate/` is empty and is exactly where Phase 6's Docker gate runner
goes.

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # Phase 5 should be committed at HEAD
git branch --show-current   # should say develop
./check.sh                  # must be green before you change anything
cosmo doctor                # core checks + harness checks in two tables
```

**Known, pre-existing environment noise on this host** (not something Phase
6 broke, don't chase it): `cosmo doctor` may show `disk space: FAIL` — this
WSL2 box runs close to the 10 GB floor. Also: this box has no *global* git
identity (`git config --global user.name/user.email` are unset — only this
repo's own local config has one) — Phase 5 worked around this for Cosmo's
own merge/rebase commits via `GitConfig.commit_author_name/commit_author_email`
passed as `-c user.name=...` per invocation; any test fixture your own work
adds that calls `git commit` needs the same treatment (see
`tests/test_git_merge.py`'s `_git` helper for the pattern). `gitleaks` was
not on PATH at the start of Phase 5 either — it's now installed at
`~/.local/bin/gitleaks` (added to this shell's `PATH` via the profile), so
`cosmo doctor`'s `gitleaks` check and `test_git_secrets.py`'s real-scan tests
should both pass; don't be surprised it's there.

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
  or drift silently. `config/model.py`'s `GateConfig._no_floating_tags`
  validator already enforces atomic version pinning for `gate.playwright_image`
  — Phase 6 is what actually makes that config section load-bearing.
- **Tests isolate from the developer's environment.** Anything touching config
  must set `COSMO_CONFIG` and `XDG_DATA_HOME` to temp paths — see the autouse
  fixture in `tests/test_cli.py`. Anything touching a real git repo should
  build one in `tmp_path`, never touch this repo or a real target repo.
- **Fake the external process, test the mechanics — except where "check by
  hand, then use the real thing" already proved out.** `FakeHarnessAdapter`
  (`cosmo.harness.fake`) is the harness test double; `fake_docker.sh`,
  `fake_claude.sh`, `fake_openspec.sh` are the subprocess test doubles
  (`tests/fixtures/`) — `fake_docker.sh` already exists from Phase 2 and is
  a real option for gate-runner unit tests, but the plan's own exit
  criterion ("a fixture Java+Spring / Vite+React repo produces a full
  structured result") asks for a real Docker run against a real fixture
  repo too — Docker itself may or may not work on this host (WSL2 Docker
  Desktop integration has been noted as flaky in earlier phases; check with
  `docker ps` by hand before assuming it works, the same way Phase 5
  checked `gitleaks`/`git worktree` semantics by hand before coding against
  them).
- **Boundary tests are load-bearing, not optional.** `test_harness_boundary.py`
  keeps harness-specific tokens out of core (`harness/claude/*.py`,
  `harness/registry.py`, and `bootstrap/symlinks.py` — added in Phase 4 —
  are the allowed exceptions, plus `config/defaults.toml`).
  `test_store_boundary.py` keeps `connect_writer` from leaking outside
  `store/writer.py` and `store/migrations.py`. `test_git_boundary.py`
  (Phase 5) keeps `cosmo.harness` out of `src/cosmo/git/*.py` (checked via
  `ast`, not text search) and keeps the literal token `master` out of
  `src/cosmo/` entirely except in `#`-comments. Check all three before
  adding any module that imports across these boundaries. The gate runner
  bypasses the harness entirely by spec (2.2) — it would be reasonable to
  extend the harness-import ban to `src/cosmo/gate/` too; your call, but
  document it either way.
- **Run `./check.sh` before committing.** All four must pass.
- **When something fails, check with a real invocation before trusting a
  unit test's green.** This has found a real bug or made a real design
  decision correctly in every phase so far — Phase 2's two worst bugs,
  Phase 3's `rate_limit_event` deviation, Phase 4's `openspec --tools
  claude` conflict, and Phase 5's git-worktree-hooks-are-shared-not-per-
  worktree finding plus the merge-vs-rebase divergence mechanism (patch-id
  empty-commit skipping) were all found this way — the last one specifically
  by running real git commands in a scratch repo *before* writing any
  ladder code, not by guessing what git would do. For Phase 6: actually run
  a real gate container against a real fixture repo (build failure, unit
  failure, e2e failure, a deliberately weakened test, an injected flaky
  test) — don't only trust the unit tests' green.

## Phase 6 scope

Spec §1.1 (container requirements), §1.2 (gate execution ordering), §1.3
(integration test layer), §6.1 layer 2 (the diff gate / test-integrity
detection), §6.4 (flaky-test handling), §9.3 (`error_detail` construction).

Summary from the plan:

1. **Docker gate runner, serial: build → unit → e2e** (§1.2), each stage
   attributed to a distinct `failure_stage` (the enum already exists,
   `store/enums.py: FailureStage` — `BUILD`, `UNIT_TESTS`, `E2E_TESTS`,
   `TEST_INTEGRITY` are already there, sitting unused since Phase 1).
2. **Non-negotiable container flags** (§1.1): `--ipc=host`, `--shm-size=2gb`
   (`config.gate.ipc_host`/`shm_size` already exist, Phase 0), and
   `--label orchestrator.run_id=... --label orchestrator.task_id=...` —
   **required** by Phase 2's `proc.orphans.sweep_containers`, which already
   filters on exactly these two label keys and has had nothing to find
   until now.
3. **Atomic version pinning** (§1.1): Playwright npm version, the
   `mcr.microsoft.com/playwright` image tag, browser binaries, and any
   cache key bump as one unit. `config.gate.playwright_image`/
   `playwright_npm_version` already exist (Phase 0) with a validator
   rejecting `:latest` or an untagged image (`GateConfig._no_floating_tags`)
   — a test should assert no `latest` tag appears anywhere in the gate
   runner's own Dockerfile/compose content, not just in config.
4. **Diff gate** (§6.1 layer 2), run *before* tests execute, against
   `git diff develop...task/<spec-id>` — you have a real git module to
   build this on top of now (`cosmo.git`), though note `attempt_merge_ladder`
   deliberately runs the *repo-level* merge/rebase, not a diff computation;
   the diff gate is a new, separate git invocation, most naturally run
   against the task's own worktree before the container even starts. Fails
   the task when `allow_test_edits` is unset (`task_queue.allow_test_edits`,
   Phase 1) and any of: a test-path file modified/deleted; net assertion
   count decreased; a skip/disable annotation introduced; test-file LOC
   dropped beyond a configured threshold. Language-specific assertion
   counting for JUnit/AssertJ and Vitest/Playwright is **Open Item 1** —
   read spec's "Open Items for Follow-Up Specs" section for the exact
   framing before guessing at a heuristic. Classified `code_error` /
   `failure_stage=test_integrity`, `error_detail` names the specific
   violation.
5. **Flaky handling** (§6.4): a version-controlled `quarantine.yml` in
   Cosmo's own repo (owner + expiry required per entry; an expired entry
   fails validation of the file itself, don't let a stale quarantine
   silently keep protecting a test). Confirm-by-rerun: a failing
   non-quarantined e2e test reruns in isolation up to 3×; a pass classifies
   `flaky` (the enum value already exists, `FailureType.FLAKY`) and
   consumes no retry attempt. Three `flaky` classifications of the same
   test across distinct runs appends to `quarantine-candidates.yml` for
   human review — **never auto-quarantine** (§6.4 step 4 is explicit about
   this; it's the same self-weakening failure mode as §6.1, just performed
   by Cosmo instead of the agent).
6. **`gitleaks` scan as gate-side backstop** (§6.1) — Phase 5 already
   installs a local pre-commit hook and gitleaks is now a `cosmo doctor`
   core check; this is the *second*, non-bypassable layer, run inside the
   gate container or against the worktree before/after the container run
   (your call — document which and why). "Any secret that reaches a commit
   is treated as compromised and requires rotation — detection is not
   remediation" (spec 6.1) — don't build auto-remediation, just detection
   and a hard fail.
7. **Structured gate result**: unit and e2e reported **separately, never
   one combined boolean** (spec 9.2), plus `flaky_detected[]` and
   `quarantined_skipped[]`.
8. **`error_detail` construction** (spec 9.3): failing test name +
   assertion + trimmed stack; build error; failing Playwright step +
   trace/screenshot **path only, never embedded binary**. Model-consumable,
   not archival — a test asserts a size ceiling.
9. **Log actual gate duration on every run** (spec 3.3's own note) so the
   45-minute `VALIDATING` timeout (`config.timeouts.validating_wall`,
   already exists) becomes empirically tunable later (**Open Item 2**) —
   this phase just needs to *record* it; retuning the default is a later
   decision once real data exists.
10. **`FakeGate` for Phases 7-8** — the same shape as `FakeHarnessAdapter`
    (`cosmo.harness.fake`): a test double later phases can drive without a
    real Docker container. This is also very likely what should satisfy
    `cosmo.git.merge.GateRerun` in Phase 5's merge ladder — re-read
    `src/cosmo/git/merge.py`'s module docstring and `GateRerun`'s type
    (`Callable[[], bool]`) before deciding whether the real gate runner's
    entry point should conform to that exact signature or whether Phase
    7/8 should wrap it. Nothing calls `GateRerun` for real yet; that's
    still open, same shape as Phase 4's `sync_harness_assets(run_id=...)`
    seam being "real, but uncalled" until its second call site landed.

### Exit criteria (from the plan)

- `cosmo validate <worktree>` on a fixture Java+Spring / Vite+React repo
  produces a full structured result.
- Fixture cases pass: green run; compile failure; unit failure; e2e
  failure; an injected flaky test correctly classified `flaky`; a
  deliberately weakened test caught by the diff gate.
- Gate durations are recorded and queryable.

## Things to know before you start

**The validation gate bypasses the harness entirely (spec 2.2).** Nothing in
`src/cosmo/gate/` should import `cosmo.harness` — consider extending
`test_git_boundary.py`'s `ast`-based import check (or writing an equivalent
`test_gate_boundary.py`) to enforce this the same structural way Phase 5
enforced "the merge ladder never sees a harness adapter."

**`FailureStage`, `FailureType`, and the `task_failures` table already exist
and are fully unused** (Phase 1 schema, `store/enums.py`). This phase is
their first real writer. Read `store/migrations.py`'s `task_failures` table
definition before designing your own result dataclasses — the columns
(`error_summary`, `error_detail`, `files_touched`, `will_retry`,
`next_action`) are effectively spec 9.3's payload shape already committed to
schema; match it rather than inventing a parallel structure.

**`config.gate.*` (playwright image/version, shm_size, ipc_host) has existed
since Phase 0 with real validators, but nothing has read it yet.** This
phase is what makes that section load-bearing — a good early sanity check is
confirming the shipped defaults actually pull and run before building the
gate runner around them.

**Phase 2's `proc.orphans.sweep_containers()` filters on
`orchestrator.run_id`/`orchestrator.task_id` labels that no container has
ever actually carried, because nothing has launched a labeled container
yet.** Once your gate runner launches its first real container with those
labels, this is a good moment to re-verify `sweep_containers` end-to-end for
real (label a container by hand, run the sweep, confirm it's force-removed)
rather than trusting Phase 2's fake-docker-only test coverage in isolation.

**`git.merge.attempt_merge_ladder`'s `gate_rerun` parameter is real but has
no real caller yet** — same seam shape as Phase 4's `sync_harness_assets
(run_id=...)` before Phase 5 gave it one. If the real gate runner's natural
entry-point signature doesn't match `Callable[[], bool]` cleanly, that's
useful information — record it as a Phase 5 spec-deviation-shaped note
retroactively rather than silently reshaping `GateRerun` without saying so
(same discipline Phase 4/5 both followed for their own seams).

**Nothing before Phase 8 should implement circuit-breaker trip logic or run
scheduling** — unchanged since Phase 3's handoff, still applies. Phase 6 is
about the gate producing a correct, structured result — not about what the
run loop does with it.

## When you finish

1. `./check.sh` green.
2. Update `v3-implementation-state.md`: mark Phase 6 complete, list what
   exists, record every decision made and anything a future session would
   otherwise rediscover. Append any new spec deviation to the cumulative
   table at the bottom.
3. Commit to `develop` with a message explaining *why*, in the style of the
   Phase 0-5 commits.
4. Rewrite this handoff for Phase 7 (task state machine, progress,
   liveness, retries) — or delete it if the next session continues
   immediately.

Phase 7 is next: the full task state machine (`QUEUED` through `DONE`,
`FAILED_RETRY`, `BLOCKED`), per-state timeouts wired to Phase 2's timers,
the progress watcher (`tasks.md` checkboxes via `watchdog`), the heartbeat,
the failure classifier, informed retries, and `COMMITTING`'s spec 11
knowledge-file step. It is the phase that finally gives Phase 5's worktree
lifecycle and Phase 6's gate real callers end to end.
