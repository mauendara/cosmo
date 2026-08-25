# Handoff — continue at Phase 4

You are picking up Cosmo mid-build. Phases 0-3 are complete. Your job is
Phase 4: the harness-facing template system, `sync_harness_assets`, root
symlinks, and `cosmo init` — plus the `PreToolUse` guardrail hooks, which are
a hard security boundary that must exist before any unattended run touches a
real repo.

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth.** v1 and v2 are superseded — read them only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map. Phase 4 is your scope (§10 in full, §2.5's hooks, Open Item 4) |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus decisions and gotchas | Read the "Things that will matter later" section under Phase 3 before writing code — the SessionStart-hook-inheritance finding is directly relevant to what you're about to build |

`v1-*` and `v2-*` in this folder are earlier spec drafts. v3 is a superset of
both. Do not implement from them.

**Do not edit the plan document.** It is the agreed scope. Record what you
build, and any decision you make along the way, in `v3-implementation-state.md`.

## Where things are

```
/home/dev/delta/cosmo/          # working branch: develop
├── docs/                       # the four documents above
├── src/cosmo/
│   ├── checks.py                 # CheckResult / CheckStatus
│   ├── config/                   # typed model, defaults.toml, three-layer loader
│   ├── doctor.py                  # core preflight checks
│   ├── harness/                  # base ABC (+cwd, +probe), registry, claude/, fake/  (Phase 3)
│   │   ├── claude/                 # adapter.py + stream.py (spec 2.3, spec 4)
│   │   └── fake/                   # FakeHarnessAdapter -- target this in every new test
│   ├── store/                    # SQLite schema, StoreWriter, reader queries (Phase 1)
│   ├── events/                   # envelope + EventEmitter, transactional sequence (Phase 1)
│   ├── proc/                     # ManagedProcess (+on_stdout_chunk), timers, orphan sweep, reap (Phase 2/3)
│   ├── cli/main.py               # `cosmo` command: config, harness (+probe), doctor, queue, events, project
│   └── {git,gate,task,run,knowledge}/   # EMPTY — later phases
├── tests/                       # 118 passing
└── check.sh                     # ruff + format + mypy --strict + pytest
```

`templates/` (top-level, alongside `src/`) does not exist yet — you are
creating it.

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # Phase 3 should be committed at HEAD
git branch --show-current   # should say develop
./check.sh                  # must be green before you change anything
cosmo doctor                # core checks + harness checks in two tables
cosmo harness probe --prompt "print hello"   # Phase 3's real end-to-end path; try it once
```

If `cosmo` is not on PATH, run `uv tool install --editable .` from the repo root.
Editable means your source edits are live — no rebuild between changes.

**Known, pre-existing environment noise on this host** (not something Phase 4
broke, don't chase it): `cosmo doctor` may show `disk space: FAIL` — this
WSL2 box runs close to the 10 GB floor.

**Read this before you write a single hook.** Phase 3's real `claude -p`
probe run showed that a headless invocation inherits the *operator's* full
user-level Claude Code config — this box's `~/.claude` plugins and
`SessionStart` hooks fired even though the probe ran against `/tmp`, nothing
to do with the cosmo project or any target repo. Decide explicitly whether
`cosmo init`/worktree sync need to isolate the child's `HOME` or
`XDG_CONFIG_HOME`, or pass `--settings` to point at an isolated settings
file, so a task running against a real target repo doesn't silently pull in
whatever the operator happens to have configured globally. This wasn't fixed
in Phase 3 because template/settings ownership is exactly your scope now —
see the Phase 3 state doc entry for the full observation.

## Conventions this codebase follows

- **Python 3.12+, `uv`-managed.** Add dependencies with `uv add`, not by hand.
- **`mypy --strict` passes.** Annotate everything, including test helpers.
- **Comments explain *why*, never *what*.** Existing comments cite the spec
  section that forced the decision. Match that.
- **Config over constants.** Every tunable goes in `config/model.py` and
  `config/defaults.toml`, annotated with its spec section. No magic numbers.
- **Validators catch what would fail silently.** See the existing timeout,
  playwright-tag, and (new in Phase 4, if you add one) template-related
  validators for the pattern: reject at startup what would otherwise
  misbehave at 3am.
- **Tests isolate from the developer's environment.** Anything touching config
  must set `COSMO_CONFIG` and `XDG_DATA_HOME` to temp paths — see the autouse
  fixture in `tests/test_cli.py`. Anything touching a real git repo should
  build one in `tmp_path`, never touch this repo or a real target repo.
- **Fake the external process, test the mechanics.** Phase 3's
  `FakeHarnessAdapter` (`cosmo.harness.fake`) is what every later phase's
  tests should target — a real `claude -p` invocation should appear at most
  once, in an integration exit criterion you run manually, exactly like
  Phase 3's `cosmo harness probe`. The one place Phase 4 might still need a
  real subprocess is `openspec` itself (step 2 of `cosmo init`) — check
  whether `openspec` has a way to run against a scratch directory without
  side effects before deciding whether it needs a fake too.
- **Boundary tests are load-bearing, not optional.** `test_harness_boundary.py`
  keeps harness-specific tokens out of core (`harness/claude/*.py` and
  `harness/registry.py` are the only modules allowed to name them, plus
  `config/defaults.toml`). `test_store_boundary.py` keeps `connect_writer`
  from leaking outside `store/writer.py` and `store/migrations.py`. Phase 4
  probably doesn't touch either boundary directly, but check before adding
  any module that imports both a harness adapter and core template code.
- **Run `./check.sh` before committing.** All four must pass.
- **When something fails, check with a real invocation before trusting a
  unit test's green.** Phase 2's two worst bugs, and Phase 3's spec
  deviation #5 (`rate_limit_event` vs. the spec's `system/api_retry`), were
  both found this way, not by inspection. For Phase 4: run `cosmo init`
  against a real scratch git repo (not just a `tmp_path` fixture) at least
  once, and manually attempt the adversarial checks the plan's exit criteria
  call for (see below) — don't only trust the hook unit tests.

## Phase 4 scope

Spec §10 in full (project bootstrap & template system), §2.5 (the
`PreToolUse` guardrail hooks themselves — Phase 3 only consumed the
`supports_gating` capability flag; nothing installs a hook yet), Open Item 4.
Summary from the plan:

1. **`templates/harness/claude/`** in Cosmo's own repo:
   - `settings.json` — `permissions.deny` for secret paths (`./.env*`,
     `./secrets/**`, `**/*.pem`, `**/id_rsa*`). Deny is used deliberately
     because it is **absolute across all permission modes** (§2.3) — this is
     the one guardrail that survives even if a future task ever justified
     `auto` permission mode.
   - `hooks/` — `PreToolUse` implementations, each **synchronous, local, no
     network, no LLM**, budgeted under 2s with `timeout: 5000` (§2.5):
     - test-path guard (`src/test/**`, `**/*.spec.ts`, `**/*.test.ts`,
       `e2e/**`), bypassed only when the task's queue row has
       `allow_test_edits: true` (that column already exists — `store/writer.py`
       from Phase 1)
     - annotation guard (`@Disabled`, `@Ignore`, `test.skip`, `it.skip`,
       `xit`, `describe.skip`)
     - commit-integrity guard (`git commit *--no-verify*`, `git push *`,
       `git reset --hard*`, force-push forms)
   - Async hooks are **not** used for gating (§2.5 — they don't block);
     telemetry only, and out of scope unless you find a concrete use.
   - `CLAUDE.md`, `agents/*.md`, `skills/*/SKILL.md` — Cosmo's harness-facing
     operating policy. This is also where the exact `propose`/`implement`
     prompt engineering that Phase 3 deliberately left thin (state doc
     decision #9 under Phase 3) actually belongs — Claude reads this file,
     Cosmo's prompt just has to point at it.
2. **`templates/projects/_blank/`** (schema-only headings) and
   **`templates/projects/java-spring-react/`** (real starter content per the
   §10.3 file list).
3. **`sync_harness_assets(target, harness)`** — one function, two call
   sites (§10.5): `cosmo init`, and worktree creation in Phase 5 (that call
   site doesn't exist yet — leave a clear seam, don't build worktree
   lifecycle early). Replaces `.agent/<harness>/` wholesale; computes a
   `template_version` hash of the source tree; emits `agent_assets.synced`
   (§9.2 — the event type already exists in `events/envelope.py`).
4. **Root symlinks (§10.2), relative only** — an absolute or cross-repo
   symlink breaks when the repo moves between the droplet and WSL2. A test
   asserts relativity (e.g. resolve the symlink target and assert it's not
   absolute, or assert the link's `readlink()` string doesn't start with `/`).
5. **`cosmo init <path> --harness claude --project-template <name>`**
   executing §10.4 steps 1-7 in order:
   1. Verify `<path>` is a git repo (do not `git init` it yourself).
   2. `openspec/` via OpenSpec's own CLI, if absent.
   3. Copy `templates/projects/<name>/docs/` into `<path>/docs/` —
      **never-overwrite** semantics, `created: N / skipped: M` reported
      explicitly, `--force` behind a confirmation prompt.
   4. `sync_harness_assets` into `.agent/<harness>/`.
   5. Root symlinks.
   6. `writer.register_project(...)` — this already exists from Phase 1
      (`cosmo project register`'s underlying call); Phase 4's `init` should
      call the same `StoreWriter` method rather than duplicating it, and
      `cosmo project register` itself might become redundant once `init`
      exists (decide whether to keep both, deprecate the standalone command,
      or leave it as a lower-level primitive — Phase 1's state doc entry
      says "treat this as the persistence primitive it already has, not
      reimplement it").
   7. Emit `agent_assets.synced`.
6. **`cosmo templates list`** — names under both `templates/harness/` and
   `templates/projects/`.

### Exit criteria

- `cosmo init` against a scratch git repo produces `openspec/`, `docs/`,
  `.agent/claude/`, correct relative symlinks, a `projects` row, and an
  `agent_assets.synced` event.
- Re-running `init` reports skipped `docs/` files and refreshes `.agent/`
  wholesale.
- Each hook is unit-tested for both deny and allow paths, and timed to
  confirm it stays under budget (the 5000ms `timeout` is a hard ceiling, not
  a target — budget under 2s per the plan).
- **A manual adversarial check**: a `claude -p` run inside a repo `cosmo
  init` just set up is genuinely blocked from editing a test file and from
  `git commit --no-verify`. This consumes a small amount of quota, same
  posture as Phase 3's probe exit criterion — run it, don't skip it. Use
  `cosmo harness probe` (Phase 3) or a small ad hoc `claude -p` invocation
  pointed at the initialized repo, with a prompt that tries the forbidden
  action and asks Claude to report whether it succeeded.

## Things to know before you start

**Phase 3 built `FakeHarnessAdapter` (`cosmo.harness.fake`) specifically so
Phase 4+ never need a bespoke test double.** If you need to unit-test
anything that calls into a harness adapter (unlikely for template/hook work
itself, but possible if you wire `cosmo init` to invoke the harness for
anything), use it. It's registered as `"fake"` in the harness registry, so
it's also reachable from the CLI (`--harness fake`) for manual dry runs.

**`ManagedProcess` now supports `on_stdout_chunk`** (a tee of the stdout
drain thread, added in Phase 3 for the stream-json reader). Unlikely to be
relevant to Phase 4 directly, but if hook testing ends up needing to watch a
subprocess's output live, this is already there — don't build a second
mechanism.

**The `allow_test_edits` queue column already exists** (`task_queue` table,
Phase 1). The test-path guard hook needs to read it, which means the hook
script needs *some* way to ask Cosmo's state — probably by reading the
task_id out of an environment variable Cosmo sets when invoking `claude -p`
(you'll need to decide what that variable is, since none exists yet) and
querying the database read-only (`store/reader.py`'s `connect_reader`,
already genuine SQLite `mode=ro`). A hook is a separate OS process from
Cosmo's own — it cannot just call into `StoreWriter` in-process. Budget
this carefully against the hook's 2s target: a SQLite read against a WAL-mode
database should be fast, but confirm it, don't assume it.

**`agent_assets.synced`'s payload (§9.2) wants a `template_version` — "a hash
of the source template tree".** No existing helper computes a directory
hash; you'll need to pick an approach (e.g. hash the sorted list of
relative-path + content hashes) and document the choice, since "hash of a
tree" is underspecified and different reasonable implementations produce
different but equally valid answers.

**Nothing before Phase 8 should implement circuit-breaker trip logic or run
scheduling** — unchanged from Phase 3's handoff, still applies. Phase 4 is
about assets existing and being enforced by the harness itself (hooks), not
about deciding what Cosmo's own loop does with a blocked task.

## When you finish

1. `./check.sh` green.
2. Update `v3-implementation-state.md`: mark Phase 4 complete, list what
   exists, record every decision made and anything a future session would
   otherwise rediscover. Append any new spec deviation to the cumulative
   table at the bottom.
3. Commit to `develop` with a message explaining *why*, in the style of the
   Phase 0-3 commits.
4. Rewrite this handoff for Phase 5 (worktree lifecycle and git operations,
   §3.2/§3.4/§6.1) — or delete it if the next session continues immediately.

Phase 5 is next: `git worktree add`, the `gitleaks` pre-commit hook per
worktree, retention/teardown policy (`DONE` removes, `BLOCKED` retains for
inspection), and — this is the real dependency Phase 4 creates — calling
`sync_harness_assets` immediately after worktree creation, before
`PROPOSING` starts, so every task runs against Cosmo's current guardrails
rather than whatever existed when `cosmo init` last ran.
