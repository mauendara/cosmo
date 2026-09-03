# Handoff — v0.1.1, three real bugs from v0.1.0's first real usage

This document was compressed for v0.1.0 forward: ~10 sessions' worth of
session-by-session narrative (what changed, what was found, how it was
fixed) has been cut. That history isn't lost — it's in `git log` (every
commit message explains its own *why*) and in
[v3-implementation-state.md](v3-implementation-state.md)'s cumulative
deviations table (the complete bug/fix log, entries 1-84). This file now
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
- **592 tests passing, 9 skipped, `./check.sh` green** as of the last code
  change. Every fix in the deviations table has a regression test.
- **Two features shipped since v0.1.1's release, both requested directly**
  (deviations 83-84 in `v3-implementation-state.md`, two separate commits
  on `private`, neither released yet): (1) console-facing timestamps now
  render in the host's local timezone (`store.clock.to_local`/
  `format_local`) instead of raw UTC — storage is unchanged, only display
  converts, applied everywhere a timestamp reaches the console plus the
  shared `events.format.event_detail` (also used by the Telegram sink).
  (2) `harness.propose_model`/`implement_model`/`review_model` let
  `PROPOSING`/`spec add`, `IMPLEMENTING`, and `REVIEWING` each run on a
  different Claude model (e.g. Opus for planning, Sonnet for
  implementation, a separate model for adversarial review), each falling
  back to the existing `harness.model` when unset. Deliberately scoped to
  global per-role config only, not per-spec/per-batch — confirmed with the
  user rather than assumed; that would need task-row persistence and new
  CLI flags, a bigger feature than asked for.
- **Cline researched as a possible new harness adapter** (driven through
  OpenRouter, so different models could be tried against Cosmo) —
  **research only, nothing implemented, no code changed this session.**
  Findings are in
  [v11-cline-harness-info.md](v11-cline-harness-info.md): real headless
  invocation confirmed working (`cline --json -P openrouter -m <model-id>`),
  a real structured JSON terminal event exists (`run_result`, with
  cost/duration/finishReason), but **no gating mechanism reachable from
  headless one-shot invocation currently works** — `CLINE_COMMAND_PERMISSIONS`
  is documented but is dead code (zero call sites anywhere in `cline/cline`,
  confirmed by cloning and grepping the real source), a vendored
  Claude-Code-shaped `--hooks-dir` hook system didn't fire in testing, and a
  third mechanism (`CLINE_TOOL_APPROVAL_MODE=desktop` file-IPC approval) is
  real and well-designed but is blocked by a specific, well-scoped ordering
  bug in `cline`'s own `run-agent.ts` (session id set after the one-shot
  turn already ran). None of this has been filed upstream yet. If this gets
  picked up again: read v11 first, and don't re-trust `docs.cline.bot`
  without cross-checking the real source — several pages there describe
  behavior that doesn't correspond to shipped code.
- **Ori Harness and OpenCode researched next, following directly from the
  Cline gating dead-end** — **research only, nothing implemented, no code
  changed this session.** Both have a real, confirmed-by-real-invocation
  pre-execution gating mechanism (the thing Cline lacked). Findings are in
  [v12-ori-opencode-harness-info.md](v12-ori-opencode-harness-info.md):
  `ori claude` is a thin wrapper that execs the real Claude Code binary
  through OpenRouter — its `PreToolUse` hooks fire and block exactly like
  native Claude Code (confirmed: a real hook denied a `Bash` call, surfaced
  in `permission_denials`, same JSON schema the existing adapter already
  parses), headless auth resolves from `OPENROUTER_API_KEY` with zero
  setup, and cancellation needs no adapter changes (Ori execs in place, no
  wrapper process survives). OpenCode has no built-in gating but a
  hand-written `tool.execute.before` plugin genuinely blocked a tool call
  under headless `--auto`, confirmed by real invocation — real work to
  build (new adapter, new template, JS/TS guardrail plugins) but the
  stronger long-term pick for testing non-Anthropic models, since its agent
  loop is provider-agnostic rather than Claude Code's loop retargeted.
  Hermes and Pi were in scope but not hands-on verified (deprioritized once
  Ori/OpenCode came back positive) — v12 records surface-level, unverified
  leads on both; Pi ships with no gating by design, Hermes's approval flow
  looks console-oriented rather than scriptable, closer to Cline's broken
  shape than to Ori/OpenCode's. If this gets picked up again: read v12
  first alongside v11.
- **The `ori-claude` harness is fully implemented and validated** —
  [v13-ori-cc-harness-template-plan.md](v13-ori-cc-harness-template-plan.md)'s
  Phases 1-6, **all done, including real-invocation validation**. Native
  Claude Code and Ori-routed Claude Code are two separate registered
  harnesses (`claude` and `ori-claude`), each with its own template, not a
  config toggle on one adapter — exactly the shape the plan's four
  user-confirmed design decisions called for: `harness/claude/invoker.py`'s
  `_ClaudeCodeInvoker` holds everything genuinely shared (`_invoke`,
  `cancel`, the `probe`/`propose`/`implement`/`review` prompts, the
  `_claude_flags` argv helper, `check_permission_mode`) so neither adapter is
  the other's base class; `OriClaudeAdapter` (`harness/ori/adapter.py`) and
  `ClaudeCodeAdapter` each declare only `preflight`/`_build_argv`/
  `_build_env`; `templates/harness/ori-claude/` is a full independent copy of
  `templates/harness/claude/`, held against drift by a byte-parity test
  (`test_harness_template_parity.py`) on the hook scripts rather than
  template-inheritance machinery; `HarnessConfig.resolve_model(harness,
  role)` adds `[harness.overrides.<name>]` tables (narrowest-first: override
  role model → override model → role model → model) since an OpenRouter
  model id is meaningless to native Claude Code and vice versa; scope stayed
  Ori+Claude only, named `ori-claude` so a future `ori-codex` needs no
  rename. Six commits on `private` so far (five code/docs + this one),
  `./check.sh` green throughout (592 tests passing, up from 570).
  - **Phase 6's real-invocation validations V1-V6 all ran for real and all
    pass**, once the user set up `ori` + `OPENROUTER_API_KEY` themselves
    (`/home/dev/.config/cosmo/.env`, 2026-09-02) and a real `[cost]
    max_cost_per_run_usd = 6.0` ceiling was added to their real
    `~/.config/cosmo/config.toml` (`write_user_config_table`, at their
    request, before the uncapped-by-default full-pipeline run). Everything
    ran against a scratch repo (`cosmo-tests/_scratch-v13-phase6`, deleted
    after), never the real store. Total real OpenRouter spend: **$1.42**.
    **V2 — the gate on the whole plan — passes**: a real prompted `Bash`
    call (`git push origin develop`) through the adapter's exact real argv
    (`--setting-sources project` included) was genuinely denied by
    `commit_integrity_guard.py`'s `PreToolUse` hook — `permission_denials`
    populated on the real terminal result. Ori's inline `--settings` does
    **not** displace the project's `settings.json` hooks;
    `OriClaudeAdapter.capabilities.supports_gating=True` is now a confirmed
    fact, not an assumption. V1/V3 also confirmed directly (stream-json +
    populated `total_cost_usd`/`session_id`; `ANTHROPIC_MODEL` honored, no
    `--model` conflict). V4 confirmed the outer process is reliably killed
    on cancel, and separately reproduced — not new, already documented at
    `proc/orphans.find_worktree_holders` — the pre-existing spec 2.4 step 4
    limitation that a bare `killpg` can't reach a `Bash`-tool-detached
    grandchild; the real `cosmo run` path already routes through
    `cancel_and_reap`'s harness-agnostic orphan sweep instead. V5: a full
    `cosmo run --harness ori-claude` (one trivial task) reached `done`
    through every real state including a real merge to `develop`, and
    incidentally reconfirmed crash recovery works identically on this route.
    V6 (informational): no rate-limit-shaped stream event appeared in any
    real terminal result, confirming the expected spec 7.2 degradation.
    **Two real, non-code findings worth knowing if this gets touched
    again**: (1) driving `ori claude` from *inside an already-running Claude
    Code session* (as this validation session itself was) leaks that
    session's own `CLAUDECODE`/`CLAUDE_CODE_*` env vars into the child and
    breaks auth ("Not logged in") — irrelevant to any real deployment, but
    strip those vars (`env -u CLAUDECODE -u CLAUDE_CODE_SESSION_ID` etc.,
    full list in `v8-validations-for-later.md`) when testing this locally
    from inside Claude Code, or it reads as a broken adapter when it isn't.
    (2) the shipped default `harness.model = "claude-sonnet-5"` is a native
    id, not an OpenRouter one — a real `[harness.overrides.ori-claude] model
    = "..."` is not optional for this harness to actually work, worth a
    callout in the docs commit below, not a code change. Full narrative:
    [v8-validations-for-later.md](v8-validations-for-later.md)'s now-resolved
    entry; deviation 86 in `v3-implementation-state.md`.
  - **What's left**: the plan's own Phase 6 docs commit — updating
    `user-docs/{en,es}/reference/config-schema.md` (the `[harness.overrides.
    <name>]` table, resolution order, and the "a real OpenRouter model id is
    required" callout above), `write-a-new-adapter.md` (the shared-invoker
    pattern, "Claude Code is the only adapter" is now false),
    `architecture-overview.md`/`quota-and-safety-model.md` (the harness table
    and quota-degradation sentence), and `README.md` if it enumerates
    harnesses. Was correctly deferred until V1-V6 actually ran (the plan's
    own commit-shape rule); nothing blocks it now.
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
- **v0.1.1 is now the first real public release.** `private` → `develop`
  was merged and pushed to the public `origin`, a PR from `develop` →
  `master` was opened and merged on GitHub, and a `v0.1.1` GitHub Release
  (tag `v0.1.1`, marked **pre-release** — not production ready) was
  published against `master`. All three local branches (`private`,
  `develop`, `master`) and `private-origin` now share this history; `git
  merge-base` confirms it, no divergence to reconcile.
- **Public `origin`'s `develop`/`master` were auto-initialized by GitHub**
  with a bare-README `Initial commit` (`afa894e`) when the repo was
  created, unrelated to this project's real history. Resolved once via
  `git merge origin/develop --allow-unrelated-histories` (keeping the
  local README, discarding the placeholder) — that merge commit is now a
  shared ancestor of `develop`/`master`/`private`, so don't be surprised to
  see `afa894e` in `git log`; it's inert.
- **Branch protection is on for `master`** on the public repo (GitHub
  ruleset): block force-pushes, restrict deletions, require a PR before
  merging (0 required approvals — solo maintainer). `develop` is not yet
  protected. The PR head branch `v0.1.1` (GitHub auto-named it from the
  local branch of the same name used for the PR) was deleted after merge,
  both locally and on `origin` — don't confuse it with the `v0.1.1` **tag**,
  which is kept.
- **Branch topology**: `private` (this branch) is the maintainer's
  day-to-day branch — CONTRIBUTING.md's branching model routes `private` →
  `develop` → the public remote, never `private` straight to public.
  `develop` is the PR-integration/release branch. `master` now tracks the
  public release line (currently == `develop` post-merge). `webapp` (a
  separate in-progress monitoring-UI feature) is missing `LICENSE` and not
  release-ready — don't push it without doing that work first.
  `.githooks/pre-push` (active via `core.hooksPath`) refuses to push a
  branch literally named `private` to whatever `origin` resolves to; it
  does **not** guard against pushing `master`/`webapp`/`develop` by habit,
  so name the branch explicitly when pushing.
- **Uncommitted, pre-existing local reformatting**: `user-docs/{en,es}/
  how-to/write-a-new-adapter.md` carry a whitespace/comment-alignment-only
  diff (inline `#` comment columns re-aligned in Python code blocks) that
  predates the 83/84 session and was deliberately left unstaged rather than
  bundled into either of that session's commits — it's unrelated to both
  features. Check `git diff` before assuming a clean tree; commit or
  discard it deliberately, don't fold it silently into an unrelated commit.
- **Remotes**: `private-origin` (`git@github.com:deltam-dev/private-cosmo.git`,
  default SSH identity) is the maintainer's private backup remote — `private`
  stays in sync with it. `origin` (`git@github.com-mauendara:mauendara/cosmo.git`)
  is the **public** repo, under a second GitHub account (`mauendara`); it
  authenticates via a dedicated SSH key and the `github.com-mauendara` host
  alias in `~/.ssh/config` (see the SSH gotcha below) — never use a bare
  `git@github.com:...` URL for it, that resolves to the wrong account's key.
  No `gh` CLI on this host; all GitHub-side actions (PR, release, branch
  protection, repo metadata) were done through the web UI.

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth** for the original 0-10 plan. v1/v2 are superseded — read only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map for what's built. **Do not edit** — record decisions in `v3-implementation-state.md` instead |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus the cumulative deviations table (1-84) and implementation-time decisions | Read the most recent deviation entries before doing anything non-trivial |
| [v4-changes-to-workflow-plan.md](v4-changes-to-workflow-plan.md) | The raw-spec-workflow feature design | Implemented — see its own Status line |
| [v5-improvements-plan.md](v5-improvements-plan.md) | Crash/pause resume, Telegram notifications, `--follow`, live-terminal observability, quota-bypass, harness failure-pattern research | Implemented — see its own Status line |
| [v6-project-template-aware-stuff-plan.md](v6-project-template-aware-stuff-plan.md) | Making the gate/failure-classifier project-template-aware, for stacks beyond Java+Spring/Vite+React | **Not started — design record only.** Needs a real second stack before it's buildable; don't start opportunistically |
| [v7-complete-queue-done-fixes-plan.md](v7-complete-queue-done-fixes-plan.md) | Closing the "queue_empty looks like done" gap | Items 1-3 done. Only item 4 (a spec-authoring question, not code) remains open |
| [v8-validations-for-later.md](v8-validations-for-later.md) | Real-invocation validations still owed | **Tracking document, not a plan.** Update an entry in place when it gets a real run |
| [v9-out-of-scope-desirables.md](v9-out-of-scope-desirables.md) | Everything declared out of scope, deferred, or still an open design decision | **Tracking document, not a plan.** Read before assuming a gap is an oversight |
| [v10-user-docs-discrepancies.md](v10-user-docs-discrepancies.md) | Where the public-docs brief described Cosmo differently from what the code does | **Tracking document, not a plan.** Read before touching `task.guardrail_tripped`, the diff gate's `test_path_modified` rule, or assuming gate stage commands are configurable |
| [v11-cline-harness-info.md](v11-cline-harness-info.md) | Research findings on Cline's CLI as a possible new harness adapter (via OpenRouter) | **Findings only, not a plan or a `HarnessCapabilities` proposal.** Nothing implemented. Read before starting real adapter work for Cline — especially the gating section, which found a real but currently-broken mechanism, not an absent one |
| [v12-ori-opencode-harness-info.md](v12-ori-opencode-harness-info.md) | Research findings on Ori Harness (`ori claude`, real Claude Code through OpenRouter) and OpenCode as possible new harness adapters | **Findings only, not a plan or a `HarnessCapabilities` proposal.** Nothing implemented. Both have real, confirmed-working headless gating, unlike Cline — read this before choosing which harness to build next |
| [v13-ori-cc-harness-template-plan.md](v13-ori-cc-harness-template-plan.md) | The build plan for `ori-claude` as a second harness adapter and template, alongside native `claude` | **Fully implemented and validated — Phases 1-6 all done**, including Phase 6's real-invocation validations (V2, the gating one, confirmed passing). See this handoff's own bullet above and [v8-validations-for-later.md](v8-validations-for-later.md). Only the plan's own Phase 6 docs commit (public user-docs updates) remains. Its four design decisions were chosen by the user, not derived — don't relitigate them |

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
- **This WSL box now has two GitHub identities.** `~/.ssh/id_ed25519`
  (default, comment `deltam.contact@gmail.com`) is the maintainer's main
  account, used for `private-origin`. `~/.ssh/id_ed25519_mauendara` is a
  second key for the `mauendara` account, used for the public `origin`
  repo, reachable only via the `github.com-mauendara` Host alias in
  `~/.ssh/config` — a bare `github.com` URL silently authenticates as the
  wrong account (fails, or worse, succeeds against the wrong repo if both
  accounts have access). Check `git remote -v` before pushing if unsure
  which identity a remote expects.
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
- **Never `git push` (including `--force`) without the user's explicit,
  per-turn approval** — see `CLAUDE.md`'s "Pushing to remotes" section.
  A prior approval doesn't carry over to the next push; state the exact
  command and wait each time.
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
   table (next number is **85**).
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
