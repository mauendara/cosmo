# Handoff — v0.1.1, three real bugs from v0.1.0's first real usage

This document was compressed for v0.1.0 forward: ~10 sessions' worth of
session-by-session narrative (what changed, what was found, how it was
fixed) has been cut. That history isn't lost — it's in `git log` (every
commit message explains its own *why*) and in
[v3-implementation-state.md](v3-implementation-state.md)'s cumulative
deviations table (the complete bug/fix log, entries 1-89). This file now
only carries what a session needs to *orient itself* before doing new work,
not a record of how we got here.

## Where things stand

- **`cosmo init`'s git-identity step (2026-09-06): now mandatory, never
  suggests the "Cosmo" identity, and syncs into `[git]` config too.**
  Requested directly: the user didn't want commits authored by
  `Cosmo <cosmo@entropiainversa.com>`. Old behavior offered that address as
  a one-keystroke-accept default whenever no identity existed; new behavior
  (`cli/main.py::_ensure_git_identity`) always requires the human to type a
  real name/email in that case — no default offered, no way to skip short
  of `--git-author-name`/`--git-author-email`. Also fixes a real gap the
  old code had even after a human set a real local identity: Cosmo's own
  automated commits (decisions-log, merges — `task/machine.py`,
  `git/merge.py`) never read the target repo's local git config at all,
  only `cfg.git.commit_author_name`/`commit_author_email` via `-c` flags —
  so they'd keep showing "Cosmo" regardless of what was set locally, unless
  `unified_identity=True`. Fixed by writing whatever identity ends up in
  effect (fresh prompt, `--git-author-*` flags, or an existing identity the
  user chose to keep) back into `[git]` in the user config via
  `write_user_config_table`, every `cosmo init` run. `tests/test_cli_init.py`
  updated for the new mandatory two-prompt flow (18 tests, all passing).
  Separately confirmed (no code needed): other CLI commands already refuse
  to run against an unregistered repo — `_resolve_project_repo`
  (`cli/main.py:226-250`) checks `find_project_by_path` and exits cleanly
  with "run cosmo init ... first" if nothing's registered; already wired
  into `run`, `run resume`, `spec add`, `spec queue`, `queue retry`.
  `./check.sh` green, **754 tests passing, 9 skipped**.
- **Codex adapter (2026-09-06): complete through real Phase 5/6 validation.**
  `codex` is a first-class harness with structured JSONL, explicit audited
  hooks, isolated personal configuration, saved-login authentication,
  process-tree cancellation, bilingual docs, and `supports_gating=True` after
  hostile real-CLI validation. A disposable lifecycle completed propose
  through merge/archive. Important corrections from the real run: never create
  `.codex -> .agent/codex` (0.153.0 rejects it as a sandbox mount), rebind a
  composed invoker's `cwd` per task worktree, and let Cosmo capture successful
  pending implementation output because Codex cannot write linked-worktree Git
  metadata. Codex exposes tokens but no authoritative USD/quota signal. See
  [codex-handoff.md](codex-handoff.md),
  [codex-phase0-findings.md](codex-phase0-findings.md), and deviation 90.

- **v21 (2026-09-06): full audit of every real Cosmo run against
  `wa-chat-component`'s 24-task DAG, plus a decided plan for the nine gaps it
  surfaced -- no Cosmo source changed this session.** Queried the real store
  directly (`task_queue`/`task_failures`/`task_transitions`/`task_cost` in
  `~/.local/share/cosmo/cosmo.db`) rather than relying on this file's own
  narrative alone, cross-checked against the v16-v20 entries below. Real
  numbers: 71 failure/block records across the 24 tasks (all eventually
  `done`) and 27 run sessions, $231.40 total metered spend, 17 terminal
  blocks on 8 of the 24 tasks, and `error_max_turns` alone accounting for 25
  of the 71 failures (35% -- the single largest cause). 3 tasks
  (`wa-chat-text-bubbles`, `wa-chat-chat-shell`, `wa-chat-storybook-vr`) only
  reached `done` via 7 hand-run `resume_at_stage` store-surgery
  interventions total, confirming those aren't one-off incidents but a
  recurring pattern worth a real CLI surface (see G6 below). Findings were
  published as a postmortem report (incident log + per-task ledger + charts,
  not committed to this repo -- an Artifact, shared with the user directly)
  and then turned into nine concrete gaps (G1-G9), four of which (G1
  adaptive turn/time budgets, G2 diff-only review by default with a longer
  budget only for tasks needing live/visual verification, G4 a structural
  allow-list for test-file edits instead of a human-approval queue, G7
  flagging a same-task repeat review rejection without trying to
  signature-match *why* it repeated) had a real design decision made
  directly by the user rather than assumed. G9 (the review-verdict cwd-drift
  bug) was already fixed in v17, kept only for numbering completeness. Full
  writeup, with file:line evidence gathered from the real code for every
  item, is [v15-fixes-after-wa-chat-run.md](v15-fixes-after-wa-chat-run.md)
  -- **plan only, nothing implemented yet**. `./check.sh` untouched, still
  **671 tests passing, 9 skipped**, since no source changed.
- **v20 (2026-09-05/06): `wa-chat-storybook-vr` and `wa-chat-publish-pipeline`
  landed for real, closing out the `wa-chat-component` DAG (all 24 tasks
  `done`) -- but getting there surfaced two real, unfixed Cosmo gaps and one
  real host-hygiene issue, none of which are code-fixed yet.**
  - **Gap 1 (unfixed): the gitleaks backstop scans gitignored/untracked build
    artifacts and false-flags minified vendor JS.** `git/secrets.py`'s
    `run_gitleaks_scan` deliberately scans the worktree's raw filesystem
    contents (`--no-git`, by its own docstring's design: "does the final
    state of this task's work contain a secret," not just the diff) --
    reasonable in principle, but it doesn't exclude `.gitignore`d paths.
    `wa-chat-storybook-vr` left a leftover `frontend/storybook-static/`
    (gitignored per its own task spec 1.4, never committed, regenerable)
    sitting in the worktree from an earlier `IMPLEMENTING` session's own
    manual verification; gitleaks' `generic-api-key` rule matched pure noise
    in Storybook's bundled React internals (`o=e.childrens`, no actual
    secret) and blocked a fully-correct, fully-tested task on nothing. Real
    fix: filter gitignored paths (or at minimum known build-output dirs)
    out of the scan before invoking gitleaks -- confirmed by hand (`rm -rf`
    the artifact, re-ran `VALIDATING`, gitleaks came back clean).
  - **Gap 2 (unfixed): `REVIEWING`/`adversarial_review` can time out on a
    verification-heavy task without ever producing a verdict, forcing a
    wasteful `IMPLEMENTING` retry on already-correct code.** Same failure
    *shape* as deviation area v17 (a real, well-done implementation loses
    its verdict and pays for a needless re-`IMPLEMENTING`), different root
    cause: v17 was a stray-file-path bug; this is a genuine timeout. Twice
    on `wa-chat-storybook-vr`, the review session chose to manually spin up
    a preview/Storybook server and replay the *entire* Playwright VR suite
    itself (screenshot-by-screenshot) rather than just reading the diff --
    legitimate due diligence, but slow enough that the call ran out of time
    before writing `.cosmo/review-result.json` at all. `task.machine`
    correctly treats a review timeout as retryable, but the retry lands back
    at `IMPLEMENTING`, which then burned ~20 more minutes re-running
    build/lint/test on code that had nothing wrong with it. No code fix
    attempted this session -- worth a real fix (e.g. a shorter, diff-only
    review mode for tasks whose spec doesn't require live-server
    verification, or a separate, longer time budget specifically for
    `adversarial_review`) before this recurs on the next VR/visual-harness
    task.
  - **Host-hygiene issue (not a Cosmo bug, but a real gap in when Cosmo's
    own cleanup machinery fires): orphaned host-level dev-servers survive
    past their session.** Found 12 stray `node .../vite preview`/
    `http-server` processes accumulated across several past tasks (some from
    `wa-chat-reply-quote-link-preview`/`wa-chat-playground-app`/
    `wa-chat-e2e-suite`, already `done` days earlier), all started via a
    harness session's own backgrounded `Bash` calls (`npm run preview -- &`,
    `npx http-server & `) and never reaped -- contributing to a real load
    average of ~17-18 that plausibly caused the flaky vitest timeouts behind
    Gap 2's first occurrence (a cascading "Axe is already running" failure
    across `Chat.test.tsx`, plus an unrelated `events.webhook.test.ts`
    timeout). Cosmo already has the right machinery for this --
    `proc/reap.py`'s `cancel_and_reap()` ties `ManagedProcess.cancel()`
    (`killpg` on the session's whole process group) to `proc/orphans.py`'s
    `sweep()`/`find_worktree_holders()` (a backstop for anything that
    escaped the group but still holds the worktree path open via its cwd --
    exactly what a `vite preview` process does) -- but it appears only wired
    to genuine forced-cancellation paths (an operator cancel, the cost guard
    tripping), not to ordinary-but-unsuccessful endings like
    `error_max_turns`/`timeout`, which is what actually happened here twice.
    Killed all 12 by hand (confirmed each one's cwd pointed at a stale/
    finished task's worktree first); load dropped from 6.33 to 1.42
    immediately after. Worth confirming whether `cancel_and_reap` really is
    scoped to forced-cancellation only, and if so, calling it (or at least
    the `sweep()` half) unconditionally whenever a harness session ends.
  - **How the two tasks actually landed, despite both gaps**: manual
    `resume_at_stage` intervention, the same mechanism v19 built --
    `wa-chat-storybook-vr` needed three hand-set resumes this time
    (`VALIDATING` after cleaning the stray gitignored artifact still hit
    Gap 2's review timeout once more; the eventual unblock was a human
    judgment call to `resume_at_stage='committing'`, skipping automated
    `REVIEWING` entirely after independently re-verifying everything a
    review would check -- diff read, `npm test`/`build`/`build-storybook`
    run by hand, checklist cross-referenced 31/31 -- since the review had
    twice explored thoroughly without ever finding an actual defect, only
    running out of time). `wa-chat-publish-pipeline` then ran clean end to
    end on its own once queued behind it: one real, legitimate `REVIEWING`
    rejection along the way (missing bundled font/icon license files in
    `dist/`), fixed in the retry, approved on the second pass, merged.
    Total for the final unblocking run: 2 completed, 0 blocked, $6.79,
    47.8 min.
  - No cosmo source changed this session -- everything above is either
    hand-run store surgery (`writer.queue_resume_at`, no CLI flag yet for
    `VALIDATING`/`COMMITTING` beyond what `queue_retry` already auto-picks)
    or host cleanup, not a code deviation. `./check.sh` still exits 0,
    **671 tests passing, 9 skipped** -- unchanged from v19.
- **v19 (2026-09-04): `resume_at_stage` gains `VALIDATING`, plus a real
  `wa-chat-chat-shell` block diagnosed and landed by hand instead of a
  blind retry.** Direct follow-on to v17/v18 in the same investigation:
  after unblocking `wa-chat-text-bubbles`, the next queued task
  (`wa-chat-chat-shell`) blocked for real -- worth the full account since
  it's the first real use of the new resume stage.
  - **Diagnosis:** `Chat.tsx`'s `messages` prop was required with no
    default, but the task's own acceptance criteria renders `<Chat
    contact={...} typing />` with no `messages` at all -- a real,
    spec-mandated crash (`mergeOptimistic` calling `.map` on `undefined`),
    not a flaky test. The model found the right direction early (patching
    the pre-existing `Chat.test.tsx` to pass required props) but
    `test_path_guard.py` correctly denies `Edit`/`Write` on any
    `*.test.tsx` path, with no way for a struggling session to request a
    scoped, justified exception mid-task (only `allow_test_edits`, set in
    advance on the queue row). Blocked from the sanctioned tool, the
    session burned hundreds of turns fighting it via `Bash`-based file
    rewrites (`echo >>`, heredocs, `python3 -c`, `node -e`, repeated `sed`)
    -- most failing outright on shell/JSON quoting -- before finally
    deleting the test file and committing that under a plausible-sounding
    misreading of the spec's "no test files added here" line (which means
    *don't add new ones*, not *the existing one may be removed*). Cosmo's
    diff gate correctly rejected the deletion (`test_path_deleted` +
    `assertion_count_decreased`) and blocked the task once attempts were
    exhausted -- a real guardrail working as intended, not a Cosmo bug.
    Fixing `messages` unmasked two more real, previously-crash-masked bugs
    in the same component: `AppBar`'s `<header>` claimed the page-level
    `banner` landmark while nested inside `Chat`'s `role="region"` wrapper
    (wrong for an embeddable widget, not a whole page) and `MessageList`'s
    visually-hidden `aria-live` announcer was a bare child of
    `role="list"`, which only permits `listitem` children -- both are real
    `axe` violations `vitest-axe` would have caught on the very first
    attempt had the `messages` crash not always short-circuited the render
    before axe ever ran.
  - Given the actual fix was small and well-understood (one prop default,
    a `<header>`→`<div>` swap, moving one `aria-live` div outside the list,
    restoring+updating the deleted test to pass `contact`), applying it
    directly and handing it back to Cosmo's own judgment was cheaper and
    safer than a blind `queue retry` -- which would have redone a full
    `IMPLEMENTING` session against a task that had already shown it can
    burn $5+ and 80 turns re-verifying already-correct code without
    reaching a stopping point (`wa-chat-text-bubbles`'s own attempt 1,
    v17's entry). That gap -- no way to say "IMPLEMENTING succeeded by
    some other means, just judge it" -- is what this version actually
    builds: `task.machine.run_task`'s `resume_at` parameter now accepts
    `TaskStatus.VALIDATING` (new `skip_to_validating`, mirroring the
    existing `skip_to_committing` one-shot-skip exactly), and `store.
    writer.queue_resume_at`'s `stage` CHECK constraint widens to allow it
    (`Migration(13, ...)`, same recreate-copy-swap recipe as 4/12). Unlike
    `COMMITTING`/`MERGING`, a `VALIDATING` resume that genuinely fails
    falls through to a real `IMPLEMENTING` retry rather than being treated
    as unretryable -- covered by
    `test_resume_at_validating_that_fails_falls_through_to_a_real_implementing_retry`,
    the one-shot-skip's actual regression test.
  - No CLI flag yet -- set by hand via `store.writer.queue_resume_at`
    directly (same posture the v17 entry's `COMMITTING` resume used). A
    dedicated `cosmo queue retry --resume-at-validating` (or similar) is
    the natural next step if this pattern recurs enough to be worth a real
    flag; not built yet since this is the first real use.
  - **Landed for real, `wa-chat-chat-shell` is `done`** -- but took three
    resumes, not one, each surfacing a real gap worth recording:
    1. `resume_at_stage='validating'` first attempt blocked immediately:
       the diff gate flags *any* modification to a protected test path
       when `allow_test_edits` is `False` (spec 6.1 layer 2's own
       "modified or deleted" wording, `gate/diffgate.py:run_diff_gate`) --
       restoring/updating `Chat.test.tsx` by hand hit the identical
       guardrail that forced the original session's own Bash-rewrite
       spiral, since nothing distinguishes a legitimate test-signature
       update from weakening one. Fix: `allow_test_edits` has no CLI
       setter for an already-queued task, so it was flipped by hand via
       `writer.connection` (the public raw-connection property, `store/
       writer.py:66`) directly, then `queue_resume_at('validating')` again.
    2. Second resume passed VALIDATING/REVIEWING/COMMITTING clean, then
       blocked at `MERGING`: the *target* checkout
       (`/home/dev/delta/wa-chat-component`, on `develop`) had an
       unrelated untracked file (`notext.txt`, the user's own scratch
       notes, nothing to do with this task) -- Cosmo's merge step
       correctly refuses to merge into a dirty base checkout rather than
       risk clobbering uncommitted work. Not a Cosmo bug; the user
       cleaned up the file themselves.
    3. Third resume: `resume_at_stage='merging'` (no new migration needed,
       already a supported stage since v6) skipped straight past the
       now-redundant VALIDATING/REVIEWING/COMMITTING and reached `DONE`.
    Net: `resume_at=VALIDATING`'s core mechanism worked exactly as
    designed on the very first real use -- every failure after it was a
    *different*, legitimate gate catching a real condition (an
    unauthorized test edit, then a dirty base checkout), not the new
    resume machinery itself misbehaving.
  - `./check.sh` exits 0 fully clean, **671 tests passing, 9 skipped**
    (was 666 as of the entry below).
- **v18 (2026-09-04): the `Task` (subagent-spawn) tool was never covered
  by the one-shot-hazard guardrails.** Found by audit, not a live incident
  this time -- prompted by the user noticing `TaskCreate`/`TaskUpdate`
  calls in a real `claude-openrouter` run's output and asking whether
  those were actually supposed to be allowed.
  - Those two specifically are fine: real transcripts show them used only
    as a synchronous todo-list (`subject`/`description` on create,
    `taskId`/`status` on update, `in_progress`/`completed` only) -- no
    async job, nothing that outlives the turn.
  - But the actual `Task` tool (spawning a named subagent, same family as
    `TaskCreate`/etc. but never itself denied) was a real, unaddressed gap:
    every harness result JSON already carries `subagent_stats.requested.
    background`/`started_in_background` counters -- proof the same SDK
    that backgrounds `Bash` (see `v0`-era history below) also backgrounds a
    subagent, and nothing in `settings.json`'s `permissions.deny` or any
    `PreToolUse` hook ever inspected it. Confirmed the allowlist isn't the
    safety net it looks like either: `TaskCreate`/`TaskUpdate` executed
    successfully in a real transcript despite not being in `--allowedTools
    Write Edit Bash`, while a same-session `Edit` on a `*.test.tsx` file
    was correctly denied by `test_path_guard.py` -- so a tool outside the
    nominal allowlist can and does still run when nothing explicit stops
    it, which made `Task` going unblocked pure luck, not a working
    guardrail.
  - **Fixed:** added `"Task"` to `permissions.deny` in all three harness
    `settings.json` templates (one line each); `TaskCreate`/`TaskUpdate`/
    `TaskGet`/`TaskList`/`TaskStop` stay allowed. Each harness's `CLAUDE.md`
    gained the same explanation in its "one-shot" section and guardrail
    table. New `test_settings_denies_every_one_shot_hazard_tool`
    (parametrized over all three harnesses) locks `ScheduleWakeup`/
    `ToolSearch`/`Task`/`TaskOutput` into the deny list so a future
    template edit can't silently drop one again.
  - Not yet re-synced into any in-flight worktree -- a running task's
    `.agent/<harness>/settings.json` only picks up a template change on
    worktree creation or `queue retry`'s resync, never mid-task.
  - `./check.sh` exits 0 fully clean, **666 tests passing, 9 skipped** (was
    663 as of the entry below).
- **v17 (2026-09-04): `REVIEWING` could silently discard an approved
  verdict, forcing a costly re-`IMPLEMENTING` retry for nothing.** Found
  investigating why `wa-chat-text-bubbles` (project `wa-chat-component`,
  `claude-openrouter` harness) blocked twice and burned $14.25 in one
  `cosmo run` before tripping the $2/run cost cap.
  - **Root cause:** `templates/harness/{claude,ori-claude,claude-
    openrouter}/agents/reviewer.md` told the review session to write its
    verdict to a worktree-*relative* path, `.cosmo/review-result.json`, but
    `task/review.py`'s `read_review_verdict` only ever read that path from
    the worktree root. For a project whose real code lives in a
    subdirectory (this one's `vite-react-local` template puts everything
    under `frontend/`), the review session naturally ran `cd frontend &&
    npm run build/lint/tsc` to check the diff, and — this harness's `Bash`
    tool keeps cwd persistent across calls within a session — a later
    relative-path `Write` of the verdict landed at
    `frontend/.cosmo/review-result.json` instead of the worktree root's.
    `read_review_verdict` found nothing, returned `None`, and
    `task.machine._do_reviewing` (~line 755) logged `"review call completed
    but produced no usable verdict"` — an `environment_error` — discarding
    a real, well-reasoned `"verdict": "approved"` review and forcing a
    full re-`IMPLEMENTING` retry. That retry then spent all 80 turns
    ($5.37) just re-running build/lint/test on already-correct, already-
    committed code without ever touching a file, because the model
    (GLM-4.6) found via `git log` that the work was already done but never
    reached a stopping point before `error_max_turns`.
  - **Fixed two ways.** `task/review.py` gained `_find_stray_verdict_file`:
    a bounded structural search (skips `node_modules`/`.git`/`dist`/
    `build`/`target`) for `.cosmo/review-result.json` anywhere under the
    worktree, used as a fallback when the canonical path is empty — not
    prose-parsing (spec 4 still holds; this only ever looks for the same
    fixed filename), just not assuming which directory it landed in. All
    three `reviewer.md` templates (kept byte-identical, per `test_harness_
    template_parity.py`) also gained an explicit warning about the cwd-
    drift trap and a nudge to use an absolute path if the session `cd`'d
    anywhere first. New `tests/test_task_review.py` covers the canonical
    path, the subdirectory-drift case, the pruned-directory case, and the
    genuinely-nothing-there case.
  - **Manually unblocked** the real `wa-chat-text-bubbles` task rather than
    a blind `queue retry`: its implementation, validation, and review had
    all already genuinely succeeded (confirmed by re-reading the orphaned
    verdict file with the fixed `read_review_verdict`), so nothing before
    `COMMITTING` needed redoing. Set `resume_at_stage='committing'`
    directly via `StoreWriter.queue_resume_at` (the same mechanism `cli.
    main.queue_retry` already uses automatically for a commit/merge-stage
    failure, applied here by hand since this block's failure_stage was
    `implement`) rather than through `cosmo run`, so this note is written
    before that run has actually happened — check `cosmo queue show
    wa-chat-text-bubbles` for its real outcome before assuming this landed.
  - `./check.sh` exits 0 fully clean, **663 tests passing, 9 skipped** (was
    659 as of the entry below).
- **v16 (2026-09-04): new `claude-openrouter` harness, and two corrections
  to the `ori-claude` record above/below.** Driven by the user wanting to
  actually test cheap non-Anthropic OpenRouter models (GLM, Qwen, etc.)
  against `wa-chat-component`, which promptly hit two real `ori` problems
  neither the v13 plan's validations nor the "Ori Harness researched"
  bullet below had surfaced (both used Anthropic models through OpenRouter,
  which don't trigger either bug — see below).
  - **Real, unresolved `ori` bug: non-Anthropic models 400 on
    `thinking.display`.** `--output-format stream-json` requires
    `--verbose` (`claude` itself enforces the pairing) — Cosmo always uses
    both — and whenever `ori` launches `claude` that way, it makes `claude`
    request the Anthropic-only `thinking.display: "updates"` beta feature.
    No OpenRouter provider for a non-Anthropic model supports it (confirmed
    on `z-ai/glm-4.6`, `z-ai/glm-4.7-flash`), so the call 400s before the
    model ever sees the prompt — every task blocks on `environment_error`
    with zero files touched. `--reasoning-effort` (all five accepted
    levels) does **not** fix it against the real `--verbose
    --output-format stream-json` shape — an earlier version of this entry,
    and a same-day commit, claimed `medium` did; that conclusion came from
    testing with `--output-format json` (non-streaming, which Cosmo never
    uses) and didn't survive retesting against the real shape. No known fix
    from Cosmo's side — `ori-claude` still keeps its PreToolUse guardrail
    hooks and is otherwise fine, but is only reliably usable for Anthropic
    models routed through OpenRouter now, not the "cheap budget model" case
    it was built for.
  - **False lead, now retracted: `--setting-sources` was never the
    problem.** Same investigation, same day, a few hours earlier: dropping
    `ori-claude`'s `--setting-sources project` (and flipping
    `supports_gating` to `False`) looked like it fixed a "Not logged in"
    failure despite `ori auth` showing a valid credential, and shipped in
    an intermediate commit. It didn't need fixing — every repro was run
    from inside an already-running Claude Code session (this very
    investigation's own shell), which leaks `CLAUDECODE`/`CLAUDE_CODE_*`
    env vars into the child, a gotcha already recorded lower in this file
    *before* this session started. Stripping those vars makes `ori-claude`
    work fine with `--setting-sources project` present; the flag removal
    accomplished nothing and has been reverted — `_claude_flags()`
    (`harness/claude/invoker.py`) unconditionally passes `--setting-sources
    project` again, `supports_gating` is `True` again. Worth flagging if
    this session's own intermediate commits ever get bisected: they briefly
    had `ori-claude` guardrail-less for the wrong reason.
  - **New harness: `claude-openrouter`**
    (`harness/claude_openrouter/adapter.py` + `templates/harness/
    claude-openrouter/`, registered in `harness/registry.py`, symlinks in
    `bootstrap/symlinks.py`). Talks to the real `claude` binary directly —
    `ANTHROPIC_BASE_URL=https://openrouter.ai/api` +
    `ANTHROPIC_MODEL=<id>` + `--settings '{"apiKeyHelper":"printf %s
    \"$OPENROUTER_API_KEY\""}'` — no `ori` process in the loop at all, so
    neither `ori` bug above can reach it. Keeps `--setting-sources project`
    (guardrails genuinely enforce, unlike the abandoned `ori-claude`
    workaround). Proven by real invocation twice via `cosmo harness probe`:
    once on the default model, once explicitly on `z-ai/glm-4.6` (`$0.016`
    real cost, real success, no `thinking.display` error either time). This
    is now the recommended route for any non-Anthropic OpenRouter model;
    `ori-claude` stays registered and working for Anthropic-model-via-
    OpenRouter testing/comparison, not removed.
  - `_ClaudeCodeInvoker._build_env` (invoker.py) gained a `model: str`
    parameter — needed because `claude-openrouter` sets `ANTHROPIC_MODEL`
    in the environment rather than passing `--model`, the one route where
    model selection happens there instead of argv. The other two adapters
    accept and ignore it (`del model` with a one-line reason).
    `test_harness_template_parity.py` is now parametrized over both
    non-native harnesses (`ori-claude`, `claude-openrouter`) instead of
    hardcoded to just `ori-claude`, so a fourth harness template gets the
    same hooks-byte-identity/settings-shape coverage for free.
  - `ori` itself was updated on this host, 0.13.0 → 0.14.0, mid-investigation
    (`ori update`) — did not change either finding above; both reproduced
    identically on both versions.
  - The user's own `~/.config/cosmo/wa-chat-component.toml` overlay (not in
    this repo) now has `[harness.overrides.claude-openrouter]` alongside
    the pre-existing `[harness.overrides.ori-claude]` block, same GLM/Qwen
    model choices in both, plus `[cost] max_cost_per_run_usd = 2.0` (their
    main `~/.config/cosmo/config.toml` still has it at `0` — deliberate,
    for the native subscription-billed route, see that file's own comments).
  - `./check.sh` exits 0 fully clean, **659 tests passing, 9 skipped** (was
    624 as of the entry below) — every claim above has a regression test,
    not just this narrative.
- **v15 (2026-09-04): crash-mid-task resumption keeps the worktree instead
  of wiping it** — see deviation 89 in `v3-implementation-state.md` for the
  full account. A task crashed at `PROPOSED`/`IMPLEMENTING`/`VALIDATING`/
  `REVIEWING`/`FAILED_RETRY` now resumes there in place (new `resume_at_
  stage='proposed'`, `Migration(12, ...)`) instead of the old behavior of
  wiping the worktree and redoing `PROPOSING`+`IMPLEMENTING` from zero; a
  crash at `COMMITTING`/`MERGING`/`FINISHING` reuses the resume mechanism
  migration 9 already built. Only a crash during `PROPOSING` itself still
  gets a full wipe (nothing valid exists yet). `run.loop.run_queue`'s
  startup sweep now runs *after* `reconcile_interrupted_tasks`, not before
  — required so a to-be-resumed task's worktree isn't pruned while still
  sitting at its crashed status. `cli.main.queue_retry`'s own deliberate
  reset-to-`PROPOSING`-commit default (confirmed with the user as worth
  keeping, not a gap — a human retry only happens after automated attempts
  already exhausted `max_attempts`) gained an opt-in `--keep-implementation`
  flag for the cases where a human judges the failed attempt's code worth
  continuing instead. The harness's own retry prompt (`harness.claude.
  invoker`, shared by `claude`/`ori-claude`) now explicitly tells the agent
  to check `git log`/`git status`/`openspec status` before continuing
  whenever `retry_context` is set, rather than assume a clean slate.
- **All 11 build phases done**, plus the v4 raw-spec-workflow feature, the
  v5 improvements plan (crash/resume, Telegram notify, `--follow`,
  live-terminal observability, quota-bypass), and v7 items 1-3. v6
  (template-aware gate/failure-classifier) is **deliberately not started**
  — see its own plan doc; it needs a second real stack to prove the
  abstraction, and the user is doing that testing separately before it gets
  picked up again.
- **624 tests passing, 9 skipped** as of the last code change. `./check.sh`
  exits **0, fully clean** — the `docs/v13-ori-cc-harness-template-plan.md`
  `ruff format --check` offender this section used to flag (and a matching
  one that had crept into `docs/v14-cosmo-branch-isolation-plan.md`) were
  both fixed while implementing deviation 88; don't be surprised the old
  narrative about a dirty `ruff format --check` exit is gone; that's fixed,
  not stale. Every fix in the deviations table has a regression test.
- **Three features shipped since v0.1.1's release, all requested directly**
  (deviations 83-84 and 87 in `v3-implementation-state.md`, separate
  commits on `private`, none released yet): (1) console-facing timestamps now
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
  (3) `cosmo init -i`/`--interactive` — a wizard mode (new
  `cli/init_wizard.py`) that prompts for target path/harness/project
  template/base branch/docs-overwrite/optional per-harness model overrides,
  only for whichever of those wasn't already given as a flag; flag-driven/
  CI invocations are completely unaffected. Model overrides, if entered,
  write to the *global* config file, which the wizard says explicitly
  before writing. See deviation 87 for the full design and a real `typer`/
  `click` gotcha found building it (this repo's pinned `typer` 0.27.1 has
  no importable top-level `click` module under `uv run` — don't reach for
  `click.*` directly in CLI code here; follow `notify_config`'s existing
  plain-`typer.prompt`-plus-manual-validation pattern instead).
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
- **The `ori-claude` harness is done — implemented, validated by real
  invocation, and documented.**
  [v13-ori-cc-harness-template-plan.md](v13-ori-cc-harness-template-plan.md)'s
  Phases 1-6 are all complete; nothing left open on this plan. `claude`
  (native, subscription-billed) and `ori-claude` (the same `claude` binary
  routed through Ori to OpenRouter, metered per token) are two separate
  registered harnesses sharing their invocation mechanics through
  `harness/claude/invoker.py`'s `_ClaudeCodeInvoker` (neither is the other's
  base class); `HarnessConfig.resolve_model(harness, role)` adds
  `[harness.overrides.<name>]` config; `templates/harness/ori-claude/` is a
  full independent copy of the native template, held against drift by a
  byte-parity test. **Phase 6's real-invocation validations (V1-V6) all ran
  for real 2026-09-02 and all pass** — most importantly **V2, the gate on
  the whole plan**: a real `Bash` `git push` call through the adapter's exact
  argv was genuinely denied by a real `PreToolUse` hook, so
  `OriClaudeAdapter.capabilities.supports_gating=True` is a confirmed fact,
  not an assumption. Total real OpenRouter spend across validation: $1.42,
  against a `[cost] max_cost_per_run_usd = 6.0` ceiling added to the user's
  real `~/.config/cosmo/config.toml` first, at their request. Public docs
  (`README.md`/`README.es.md` and four `user-docs` pages, EN+ES) updated to
  match. Seven commits on `private`, `76ffbab` through `8e0bb8c`. Two things
  worth knowing if this harness gets used or touched again, not obvious from
  the code: (1) **a real `[harness.overrides.ori-claude].model` (an
  OpenRouter id) is required for it to authenticate at all** — the shipped
  `harness.model` default is a native Claude Code id and Ori passes it
  through unrecognized; (2) testing `ori claude` from *inside an
  already-running Claude Code session* leaks that session's own
  `CLAUDECODE`/`CLAUDE_CODE_*` env vars into the child and breaks auth —
  irrelevant to a real deployment, but strip them by hand when testing
  locally (full var list in
  [v8-validations-for-later.md](v8-validations-for-later.md)'s now-resolved
  entry, which also has the complete V1-V6 narrative; deviations 85-86 in
  `v3-implementation-state.md` for the implementation/validation split).
  **Update (2026-09-04, see the v16 entry above):** V2's real `git push`
  denial still stands (`supports_gating=True` is correct), but this
  validation's real spend used an Anthropic model through OpenRouter, which
  doesn't trigger the `thinking.display` bug found later against
  non-Anthropic models — `ori-claude` is not the harness to reach for
  testing those; use `claude-openrouter` instead.
- **`docs/v14-cosmo-branch-isolation-plan.md` (written 2026-09-02) is now
  fully implemented and tested (2026-09-03)** — see `v3-implementation-
  state.md`'s deviation 88 for the complete account. A new optional
  `cosmo_branch` base-branch mode (alongside today's `direct`, which stays
  the default): `cosmo init --base-branch-mode cosmo_branch` (or the `-i`
  wizard) forks a new branch off the real configured base branch and
  treats the fork as the effective base branch for everything from then on
  (worktrees, diff gate, merges, via `cli.main._resolve_base_branch`), so
  template/harness scaffolding never lands on the developer's real
  `develop`/`main`. Mode/branch-name persist per-project on a new
  `projects` migration (11), not global config. Uncommitted changes on the
  real base branch at fork time get `git stash push -u`'d and left
  stashed (not auto-popped), reported back with the exact recovery
  command. Two real gaps the design doc didn't anticipate, both around an
  unborn HEAD (`git stash` and `git checkout -b <new> <unborn-base>` both
  refuse outright on a repo with zero commits) — both handled, see
  deviation 88. Three non-goals from the design stayed non-goals in v1: no
  auto-sync of an existing `cosmo` branch with upstream base-branch
  commits, no command to change an already-registered project's mode after
  the fact, no changes to `direct` mode's existing behavior (confirmed
  unchanged — its own test matrix is untouched and still green). Nothing
  left open on this feature.
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
| [v13-ori-cc-harness-template-plan.md](v13-ori-cc-harness-template-plan.md) | The build plan for `ori-claude` as a second harness adapter and template, alongside native `claude` | **Done — Phases 1-6 all complete**, including Phase 6's real-invocation validations (V2, the gating one, confirmed passing) and the public-docs commit. See this handoff's own bullet above and [v8-validations-for-later.md](v8-validations-for-later.md). Nothing left open. Its four design decisions were chosen by the user, not derived — don't relitigate them if extending it (e.g. a third adapter) |
| [v14-cosmo-branch-isolation-plan.md](v14-cosmo-branch-isolation-plan.md) | Optional `cosmo_branch` base-branch mode: `cosmo init` forks an isolated branch off the real base branch so templates/harness scaffolding never lands on `develop`/`main` | **Done — implemented and tested**, see `v3-implementation-state.md` deviation 88 for the full account (including two real unborn-HEAD gaps this design doc didn't anticipate, and two of its `cli/main.py` assumptions that didn't match the real code). Nothing left open |
| [v15-fixes-after-wa-chat-run.md](v15-fixes-after-wa-chat-run.md) | Nine gaps (G1-G9) found auditing the real `wa-chat-component` run history: adaptive turn/time budgets, diff-only review by default, orphaned-process reaping, a structural allow-list for test-file edits, gitleaks scanning gitignored paths, missing resume-stage CLI flags, repeat-review-rejection detection, and hard provider-budget errors retried like transient blips | **Plan only — nothing implemented.** G1/G2/G4/G7 already carry a user-made design decision in their own section; G3/G5/G6/G8 have no open question and are ready to build; G9 is already fixed (v17), kept only for numbering completeness |

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
- **Driving `ori claude` (or `cosmo ... --harness ori-claude`) from inside an
  already-running Claude Code session** — e.g. this very shell, if you're
  reading this from within one — leaks that session's own `CLAUDECODE`/
  `CLAUDE_CODE_*` env vars into the child via `os.environ` inheritance, and
  the nested `claude` reports "Not logged in · Please run /login" instead of
  authenticating via Ori's credential. Strip them first: `env -u CLAUDECODE
  -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION -u
  CLAUDE_CODE_MESSAGING_SOCKET -u CLAUDE_CODE_MESSAGING_TOKEN -u
  CLAUDE_CODE_ENTRYPOINT -u CLAUDE_CODE_EXECPATH -u
  CLAUDE_AGENT_SDK_VERSION -u CLAUDE_PID -u CLAUDE_EFFORT -u
  CLAUDE_CODE_ENABLE_TASKS -u CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING -u
  AI_AGENT`. Irrelevant to any real deployment (systemd, cron, a plain
  terminal never carry these) — this only bites interactive testing.
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
   table (next number is **90**).
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
