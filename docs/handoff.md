# Handoff — continue at Phase 10

You are picking up Cosmo mid-build. **Phases 0-9 of the original plan are
complete, and the v4 workflow-changes feature (raw-spec → enrich → propose
→ apply → review → finish/archive, folded entirely into `cosmo` commands)
is also complete.** Phase 10 — the original plan's last phase — is the only
thing left. This is not a code-writing phase in the same sense as 0-9 or
v4: acceptance — a real target repo, 5-10 genuine OpenSpec changes (or, now
that v4 exists, real `cosmo spec add`/`cosmo spec queue` fan-outs — your
call, see "A real decision this session left open" below), run unattended
overnight under systemd, post-run review against the spec's own claims.

**Multiple sessions of genuine prep happened ahead of Phase 10 itself** (not
the acceptance run — Phase 10 proper is still not started): a new project
template (`templates/projects/vite-react-local/`), real gate bugs it
surfaced and fixed, a project-agnostic guardrail widening, an agent-template
polish pass, a Claude Code attribution setting, a `cosmo init` git-identity
step, a real headless-permissions bug (`--allowedTools`, deviation 38) found
running `cosmo spec add` for real, a `cosmo spec add` idempotency fix
(deviation 40), and a `gate.frontend_image` bump to `node:24.19-bookworm`
plus toolchain-version-pinning doc guardrails (deviations 41-42) found by
the user's own real `cosmo run` against `vite-react-local`. None of this is
Phase 10 scope creep — read the state doc's "Phase 10 prep" section (right
after "v4 workflow changes — Complete") in full before doing anything; it
has grown across sessions and is no longer short, and several of its
decisions (especially the git identity one, and the permissions/toolchain
fixes) change what "Get oriented" below used to say.

**A real `cosmo run` already happened against `/home/dev/delta/cosmo-tests/
todo-frontend-app` (run `fb254309566b4de0817847e29b455ab6`, real cost
$4.66) before deviations 41-42 landed** — `scaffold-app` is `BLOCKED`
(`code_failure`, 3 failed attempts) and 4 more tasks are stalled behind it;
see the state doc's "Decisions made" prose for the full failure chain. Once
the Docker gate image bump is confirmed (real opt-in `COSMO_GATE_DOCKER_E2E=1`
suite, in progress as this was written), `cosmo queue retry scaffold-app`
+ `cosmo run` against that same repo is the natural next real-world check —
worth doing before Phase 10 proper, not folded silently into it.

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth** for the original 0-10 plan. v1 and v2 are superseded — read them only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map for Phase 10 (its own section, near the end). **Do not edit** — it's the agreed scope; record decisions in `v3-implementation-state.md` instead |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus decisions and gotchas | Read the "Phase 9 — Complete" **and** "v4 workflow changes — Complete" sections in full before doing anything — several of their decisions and open items are load-bearing for Phase 10 |
| [v4-changes-to-workflow-plan.md](v4-changes-to-workflow-plan.md) | The raw-spec-workflow feature design | Implemented — see its own Status line. Read it for *why* the `REVIEWING`/`FINISHING` states and `cosmo spec` commands are shaped the way they are; read the state doc's v4 section for what's actually real |

`v1-*` and `v2-*` in this folder are earlier spec drafts. v3 is a superset of
both. Do not implement from them.

Two more files in this folder are historical, already fully consumed —
don't re-read them looking for open work: [simple-template-handoff.md](simple-template-handoff.md)
scoped the `vite-react-local` template, now built (see the state doc's
"Phase 10 prep"); `old-agents-skills/` is the user's pre-Cosmo Claude Code
skill/agent files, mined once for ideas that fit Cosmo's headless model
(see the same state-doc section for what was kept vs. discarded).

## Where things are

```
/home/dev/delta/cosmo/          # working branch: develop
├── docs/                       # the four documents above
├── deploy/                     # Phase 9: cosmo-run.service, README (install notes, WSL2 caveat)
├── templates/                  # harness + project templates (source of truth)
│   ├── harness/claude/
│   │   ├── CLAUDE.md                         # 10 prep: polished, attribution.commit note added
│   │   ├── settings.json                     # 10 prep: attribution.commit="" -- no Claude co-author trailer
│   │   ├── agents/{implementer,reviewer}.md  # v4 + 10 prep: reviewer.md gains tools: (no Edit) + technique bullets
│   │   ├── hooks/test_path_guard.py          # 10 prep: PROTECTED_PATTERNS gains .tsx/.jsx
│   │   └── skills/spec-enrichment/SKILL.md   # v4 + 10 prep: cosmo spec add's own harness call
│   └── projects/
│       ├── _blank/, java-spring-react/       # pre-existing
│       └── vite-react-local/                 # 10 prep: new -- frontend-only, localStorage, no backend
├── src/cosmo/
│   ├── checks.py, doctor.py, config/, harness/
│   ├── bootstrap/git_identity.py # 10 prep: new -- cosmo init's target-repo git identity step
│   ├── watchdog.py               # Phase 9: sd_notify, hand-rolled, no dependency
│   ├── retention.py              # Phase 9: paths.log_dir rotation (7d done / 30d blocked)
│   ├── git/merge.py              # Phase 5: worktree lifecycle, merge ladder -- 10 prep: author: tuple|None
│   ├── gate/runner.py            # Phase 6: the Docker validation gate -- 10 prep: e2e no longer needs backend_dir
│   ├── task/                     # Phase 7: the per-task state machine
│   │   ├── machine.py               # v4: _do_reviewing/_do_finishing; 10 prep: unified_identity branch
│   │   └── review.py                # v4: new -- the .cosmo/review-result.json verdict-file contract
│   ├── spec/                     # v4: new -- *-task.md frontmatter parsing (taskfile.py)
│   ├── knowledge/                # Phase 7: spec 11's COMMITTING-step guardrails
│   ├── run/                      # Phase 8/9: run-level state machine, DAG, breaker, quota, cost
│   │   └── loop.py                 # unchanged by v4 (plan's own "zero changes needed" argument, confirmed true)
│   ├── store/                    # SQLite schema, StoreWriter, reader queries
│   │   ├── migrations.py            # 6 migrations now -- 4-6 are v4 (task_queue.status widened,
│   │   │                              task_failures.failure_stage widened, spec_batch_id column)
│   │   └── enums.py                 # v4: TaskStatus.REVIEWING/FINISHING, FailureStage.ADVERSARIAL_REVIEW
│   ├── events/                   # envelope + EventEmitter + emit_state_changed
│   ├── proc/                     # ManagedProcess, WallClockTimer/StallTimer/LivenessTimers, orphan sweep
│   └── cli/main.py               # `cosmo` command -- v4 added `cosmo spec add`/`cosmo spec queue`;
│                                    10 prep added the git-identity step to `init`
├── tests/                       # 387 passing + 8 opt-in real-Docker/real-openspec
│   └── fixtures/gate_repo/        # real Spring Boot + Vite/React fixture, reusable for your own tests too
└── check.sh                     # ruff + format + mypy --strict + pytest
```

Nothing empty is waiting for you the way `cosmo.run` was for Phase 8, or
`deploy/` was for Phase 9 — Phase 10 is not a code-writing phase in the same
sense as 0-9/v4. It is: seed real work into a real target repo, run the
thing for real, unattended, overnight, then write down what actually
happened. Whatever code changes *do* come out of it should be small and
targeted — a real bug the overnight run surfaces, or Open Item 2's timeout
retuning, not new features.

## A real decision this session left open

The plan's own Phase 10 scope (below, carried over verbatim from before v4
existed) says "5-10 genuine OpenSpec changes, queued the old way." Now that
`cosmo spec add`/`cosmo spec queue` exist and are the *documented* front
door (`cosmo queue add` still works, but is no longer what a real user is
pointed at), running Phase 10 entirely through the old direct-OpenSpec path
would prove the overnight loop but never actually exercise `REVIEWING`/
`FINISHING` for real — both still have **zero real-`claude -p`
verification** (see the state doc's v4 "Things that will matter later"). The
raw-spec fan-out half (`cosmo spec add`) *is* now verified for real, same
session as the git-identity work: it uncovered and fixed a real bug where
`dontAsk` mode could never write files at all in a headless worktree
(Claude Code's workspace-trust gate silently discards `permissions.allow`
from `.claude/settings.json`; fixed via `--allowedTools` on the adapter's
own argv — see the state doc's "Phase 10 prep" deviation 38). A mixed run — some tasks via `cosmo spec add`/`spec
queue`, some via direct `cosmo queue add` — would cover more real ground in
one overnight shot than either alone. This is a real, unresolved call for
whoever runs Phase 10, not a decision already made on your behalf — confirm
with the user before assuming an approach, especially since `review.
enabled=true` by default means every task in the run will hit a real
reviewer session regardless of which front door queued it.

A ready-made source for the target repo's units of work, if you want one
that doesn't require inventing scope from scratch: [simple-template-handoff.md](simple-template-handoff.md)'s
Part 2 lists six small greenfield app ideas (todo list, habit tracker,
Pomodoro timer, Memory game, Snake/2048, expense tracker), each scoped to a
small task count and designed to isolate Cosmo's own loop behavior from
stack complexity. They all target the now-built `vite-react-local` template
-- `cosmo init <target> --project-template vite-react-local` is real and
verified this session. Using them isn't mandatory, but they're there if
useful.

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # v4 workflow changes + this session's Phase 10 prep should be at HEAD
git branch --show-current   # should say develop
./check.sh                  # must be green before you change anything
cosmo doctor                # core checks + harness checks in two tables
```

**Known, pre-existing environment noise on this host** (not something a
prior phase broke, don't chase it): `cosmo doctor` may show `disk space:
FAIL` — this WSL2 box runs close to the 10 GB floor at the *test* data path
it checks (`/tmp` is a small tmpfs on this box); the real filesystem has
hundreds of GB free. This box still has no *global* git identity (only this
repo's own local config has one), and that's now less of a trap than it
used to be: `cosmo init` against your real target repo seeds one
automatically (`bootstrap.git_identity`, "Phase 10 prep" in the state doc —
`Cosmo <cosmo@entropiainversa.com>` by default, or it'll prompt if the
target already has an identity). It's still true for a *test fixture* your
own work adds directly (not through `cosmo init`) — any such fixture that
calls `git commit` still needs `-c user.name=...`/`-c user.email=...`
passed explicitly, same as every existing test in this repo does. `gitleaks`
is on PATH, `docker` works, and so is the real `openspec` CLI (confirmed for
real this session — `openspec new change`/`openspec archive --yes` both work
against a scratch repo, see the state doc's v4 "Real invocations"
subsection).

**This host's WSL2 genuinely has systemd enabled** (`/etc/wsl.conf`'s
`[boot] systemd=true`, confirmed for real in Phase 9 — `ps -p 1 -o comm=`
reports `systemd`, `systemctl --user` works). This is exactly what Phase
10's "run unattended overnight under systemd" exit criterion needs — it is
testable here, not just on a real droplet. See `deploy/README.md` before
installing the unit; it documents the exact `Restart=`/
`RestartPreventExitStatus=` reasoning and how it was verified for real in
Phase 9 (throwaway `systemctl --user` units, not just read the docs).

**One real environment gotcha from Phase 6 remains, one is now fixed** —
read Phase 6's state-doc section for the full diagnosis before you touch
anything Docker- or npm-related. Still true: **`npm install` can hang
indefinitely on this host if a previous run was killed mid-install** (fix:
verified-clean `rm -rf node_modules package-lock.json` first, not waiting
longer). Fixed by the Phase 9 fast-follow: **Docker containers write
bind-mounted build artifacts as root**, which used to block a later
unprivileged `rm -rf` and was worked around by hand with a throwaway
`alpine` container every time — `git.worktree.remove_worktree` now does
that itself (see the state doc's Phase 9 "Fast-follow" decision 9).

One more: **this session's shell may have `XDG_DATA_HOME=/tmp/cosmo-test/data`
set** (sandboxing `cosmo`'s own runtime state away from the real home
directory). `uv run cosmo ...` (this project's own `.venv`) is unaffected by
this and is the more reliable invocation for anything scripted; if you ever
need `uv tool install --editable .` again, run it as `env -u XDG_DATA_HOME
uv tool install --editable --force .` or it will reinstall into the wrong
place and leave `~/.local/bin/cosmo` dangling.

**From Phase 9, worth knowing before an overnight run:**

- `git.worktree.sweep_stale_worktrees` is wired into `run.loop.run_queue`'s
  startup section; `cosmo events tail` has `--payload`/`--type`; `cosmo
  queue failures <task-id>` exists for diagnosing a blocked task through the
  CLI alone — use these for the overnight run's own post-run review, not
  raw sqlite queries.
- **`WatchdogSec` in the shipped unit is 10800s (3h), task-boundary
  granularity, not task-internal** (Phase 9 decision 7/8) — a single
  wedged `IMPLEMENTING`/`VALIDATING`/`REVIEWING` attempt is only caught at
  the *next* task-boundary ping, not immediately. If the overnight run
  needs tighter detection, that's a real Phase 10 finding to record.
- **The circuit breaker's tally and quota heuristic's consecutive-failure
  count still live in-memory inside one `run.loop.run_queue` call** —
  unchanged since Phase 8. A systemd-restarted run (post-watchdog-kill or
  a clean `on-failure` case) starts these counters from zero again, same
  as a hand-restarted one.
- **No CLI command to resume a `PAUSED` run** — still true. `cosmo report`
  makes a paused run's state legible after the fact but doesn't add a
  resume path; re-running `cosmo run` starts a fresh `run_id`.
- **Quota heuristic and secondary-signal config values are still
  unverified guesses** (`quota.heuristic_consecutive_threshold`/
  `heuristic_max_duration_seconds`/`result_error_subtypes`, Phase 8
  decisions 4/5) — an overnight run is specifically positioned to confirm
  or falsify these for real.
- **`review.enabled`/`timeouts.reviewing_wall` (v4) are equally unverified
  guesses**, same posture — no real `claude -p` review-call duration data
  exists yet. If the overnight run is the first time `REVIEWING` actually
  runs for real, this is exactly the data Phase 10 should capture.

## Conventions this codebase follows

- **Python 3.12+, `uv`-managed.** Add dependencies with `uv add`, not by hand.
- **`mypy --strict` passes.** Annotate everything, including test helpers.
- **Comments explain *why*, never *what*.** Existing comments cite the spec
  section that forced the decision. Match that.
- **Config over constants.** Every tunable goes in `config/model.py` and
  `config/defaults.toml`, annotated with its spec section. No magic numbers.
- **Tests isolate from the developer's environment.** Anything touching
  config must set `COSMO_CONFIG` and `XDG_DATA_HOME` to temp paths — see the
  autouse fixture in `tests/test_cli.py`/`test_cli_run_queue.py`. Anything
  touching a real git repo should build one in `tmp_path`, never touch this
  repo or a real target repo. Retry-driven tests should override
  `retries.delay_min`/`delay_max` to `0` via `cfg.model_copy(...)`. Any test
  exercising `run.loop.run_queue`/`task.machine.run_task` for real must also
  override `disk.min_free_gb` down near zero (Phase 9) **and
  `review.enabled=False` unless it's specifically testing `REVIEWING`** (v4)
  — see `_fast_config` in `test_run_loop.py`/`test_task_machine.py` for both.
- **Fake the external process, test the mechanics — except where "check by
  hand, then use the real thing" already proved out.** `FakeHarnessAdapter`
  and `FakeGate` are the two test doubles later phases should target
  directly. Real-process/real-Docker/real-`openspec` tests exist but are
  skip-guarded (`COSMO_GATE_DOCKER_E2E=1`, or a `which openspec` check) because
  they take real time or need a real binary on PATH. Phase 10's own overnight
  run is the largest instance of this pattern in the whole project — there is
  no way to fake your way through an acceptance phase.
- **Boundary tests are load-bearing, not optional.** `test_harness_boundary.py`,
  `test_store_boundary.py`, `test_git_boundary.py`, `test_gate_boundary.py`.
- **Run `./check.sh` before committing.** All four must pass.
- **When something fails, check with a real invocation before trusting a
  unit test's green.** This has found a real bug or made a real design
  decision correctly in every phase so far, v4 included (see its own state-
  doc section's "Decisions made during this work" — several were only found
  by writing the real code, not by re-reading the plan more carefully).

## Phase 10 scope

Per the plan's own "Acceptance: unattended overnight run" section:

1. Point Cosmo at a real target repo initialized by `cosmo init`. Queue
   5-10 genuine units of work with real `depends_on` edges (see "A real
   decision this session left open" above for which front door(s) to use).
2. Run unattended overnight under systemd (`deploy/cosmo-run.service`,
   Phase 9) with production config (a real `COSMO_CONFIG` pointing at
   non-XDG paths, not the dev defaults).
3. Post-run review against the spec's own claims: did anything reach
   `DONE` without a passing gate; did any test get weakened; were any
   orphan processes/containers left; did quota handling behave; are the
   p95 gate numbers consistent with §3.3's defaults; if `REVIEWING` ran for
   real, did it produce usable verdicts (both accept and at least one real
   reject, if the run's own tasks happen to produce one) rather than
   degrading to the "no usable verdict" environment-error path every time.

### Exit criteria (from the plan)

- A full night's run completes with a coherent `run.summary` and an event
  log sufficient to reconstruct every decision without reading a raw log
  (`cosmo report` and `cosmo events tail`, both already built, are your
  tools for this — if either turns out insufficient for real post-run
  review, that's a real Phase 10 finding).
- **Open Item 2** closed: §3.3 timeouts retuned against real p95 data (and,
  if `REVIEWING` ran for real, `timeouts.reviewing_wall` alongside them), or
  explicitly confirmed as-is with real data behind the confirmation.

## When you finish

1. `./check.sh` green (if any code changed at all).
2. Update `v3-implementation-state.md`: mark Phase 10 complete, record the
   overnight run's real findings (not a summary of what was *supposed* to
   happen — what actually did), and append any new spec deviation to the
   cumulative table (next number is 39 — this session's prep work used
   34-38).
3. Commit to `develop` with a message explaining *why*, in the style of
   the Phase 0-9/v4 commits.
4. This is the last phase in the original plan — there is likely no further
   handoff to write. If real work remains (the worktree sweep, watchdog
   granularity, a resume-paused-run command, the still-unverified `REVIEWING`
   real-invocation gap, or anything the overnight run itself surfaced),
   record it as an open item in the state doc rather than inventing a new
   phase number the plan never named.
