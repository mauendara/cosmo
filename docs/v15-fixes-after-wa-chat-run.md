# Cosmo — v15: fixes found auditing the wa-chat-component run

## Status

**All nine implemented.** G1, G2, G3, G4, G5, G6, G7, G8 landed 2026-09-07
(v22, deviations 91-93 in `v3-implementation-state.md`), plus the shared
consecutive-failure-counting building block G1/G7/G8 all reuse; G9 shipped
earlier (2026-09-04, see `docs/handoff.md`'s v17 entry). Nothing left open
on this plan.

## Context: where this plan comes from

A full audit of every real Cosmo run against the `wa-chat-component` project
(24 tasks, all eventually `done`), driven from the real store
(`~/.local/share/cosmo/cosmo.db`'s `task_queue`/`task_failures`/
`task_transitions`/`task_cost` tables) cross-checked against
`docs/handoff.md`'s v16–v20 narrative. Headline numbers: **71 failure/block
records** across 24 tasks and 27 run sessions, **$231.40** total metered
spend, **17 terminal blocks** on 8 of the 24 tasks, and **3 tasks that only
landed via 7 hand-run `resume_at_stage` store-surgery interventions** because
no CLI flag exists for it. `error_max_turns` alone is 25 of the 71 failures
(35% — the single largest cause by far). The full incident-by-incident
breakdown lives in the postmortem artifact this plan follows from; this
document only covers what to actually build in response.

Nine gaps came out of that audit, numbered G1–G9 below in the order they were
discussed and decided, not by severity.

## Shared building block: per-task consecutive-failure counting

G1, G7, and G8 all reduce to the same underlying question — *"has this
specific task failed the same way, N times in a row, right now"* — so this
should be one helper, not three. `store/failure_signature.py` already has
half of this: `classify_failure_signature` (deterministic substring
matching against `error_detail`) and `detect_repeat_block` (groups a task's
own `task_failures` history by signature, or by the coarser
`(failure_stage, error_summary)` fallback when no signature matches). Today
it's wired into exactly one call site — `cli.main.queue_retry`
(`src/cosmo/cli/main.py:1754`) — and it only ever looks at rows where
`next_action == "block"` (`store/failure_signature.py:121`), which is why it
never saw the interactive-buttons-cta case at all: both of its adversarial
review rejections had `next_action = "retry"`, never `"block"`.

The shared piece worth building once: a variant that counts *consecutive*
same-shaped failures regardless of `next_action`, scoped to a single task's
full history (not just the current run), that G1/G7/G8 each call with a
different key function and a different threshold. Doesn't need to be a new
abstraction if `detect_repeat_block` can just grow a `require_block:
bool = True` parameter — worth checking which is less invasive before
building a parallel function.

---

## G1 — adaptive turn/time budget for `IMPLEMENTING` *(implemented, 2026-09-07)*

**Problem.** Every task gets the same fixed `harness.max_turns = 80` /
`timeouts.implementing_wall = 5400s` (`config/defaults.toml:14,47`),
regardless of shape. `wa-chat-storybook-vr` hit `error_max_turns` **six
times** before its first real `VALIDATING`; Storybook + a Playwright VR
harness is simply more turn-hungry than a scaffold or a hook task, and
nothing in the config lets that vary.

**Decision (made by the user):** adaptive, not static per-template. Widen
the budget after repeated `max_turns` failures on the *same* task, rather
than trying to pre-classify which task templates need more room. Rely on the
existing `cost.max_cost_per_task_usd` ceiling (`config/defaults.toml:80`,
currently `0.0` = disabled) as the real backstop against a task that's stuck
in an unproductive loop burning an ever-larger budget for nothing — not a
new turn-count cap invented to do the same job.

**Design sketch.**
- New config, `[retries]` section (`config/defaults.toml:60`, `config/model.py`
  near `RetryConfig`): `turn_budget_growth_factor` (e.g. `1.5`) and
  `turn_budget_max_multiplier` (e.g. `3.0`, so 80 turns can grow to 240 but
  no further) — same shape for `implementing_wall`/`implementing_stall` so
  the wall-clock budget grows in step with the turn budget rather than one
  becoming the binding constraint while the other doesn't move.
- Consumption point: `harness.claude.invoker.py:148` reads
  `self.config.harness.max_turns` directly into `_build_argv`; it needs to
  become a value resolved per-attempt instead of a flat config read.
  `task/machine.py:570` (`_do_implementing`) is where the attempt is kicked
  off and where `attempt_count`/failure history for this task is already in
  scope — the natural place to compute "how many consecutive
  `error_max_turns` has *this task* logged" (via the shared helper above,
  keyed on `failure_stage="implement"` + a new `max_turns_exhausted`
  signature in `_SIGNATURES`) and pass a scaled `max_turns`/`implementing_wall`
  down into the invoker call rather than reading config directly.
- `max_turns_exhausted` needs adding to `store/failure_signature.py`'s
  `_SIGNATURES` taxonomy (currently `error_max_turns` failures carry no
  `error_detail` at all per `store/failure_signature.py:79-82`'s own comment
  about `scaffold-app`'s history — the classifier would need to match on
  `error_summary` instead of `error_detail` for this one case, which
  `classify_failure_signature`'s current signature doesn't support; likely
  needs its own small helper rather than forcing it through the existing
  substring-matcher).
- Scope: only grow the budget on a *repeat* `max_turns` exhaustion for the
  same task, never on the first attempt — the first 80-turn ceiling stays
  exactly as it is today.

---

## G2 — diff-only review by default, longer budget only for visual-verification tasks *(implemented, 2026-09-07)*

**Problem.** `reviewing_wall = 900s` (`config/defaults.toml:53`) is enough to
read a diff, not enough for a review session that (reasonably) decides to
spin up a preview server and replay an entire Playwright VR suite
screenshot-by-screenshot — which happened twice on `wa-chat-storybook-vr`,
each time discarding a review that had found no real defect and forcing a
wasted ~20-minute re-`IMPLEMENTING` pass. The reviewer template
(`templates/harness/*/agents/reviewer.md`) grants `Bash` and never tells the
session *not* to run the app — nothing currently distinguishes "this task
needs live verification" from "reading the diff is enough."

**Decision (made by the user):** diff-only by default; an explicit longer
`adversarial_review` budget only for tasks whose spec calls for visual/live
verification.

**Design sketch.**
- Needs a signal on the task itself for "this one needs live verification" —
  the natural place is the OpenSpec task/spec content (e.g. a task whose
  `tasks.md` includes a Playwright-VR or Storybook acceptance step), read at
  `REVIEWING` entry the same way `_do_reviewing` already reads
  `spec_path`/`tasks.md` for the review prompt (`task/machine.py`, review
  invocation around the `_ClaudeCodeInvoker.review` call in
  `harness/claude/invoker.py:246-274`). Simplest version: a keyword/pattern
  check against the task's own spec content (e.g. "storybook", "visual
  regression", "playwright" in an acceptance-criteria line) rather than a
  new queue-row column — avoids a migration, and every real case behind this
  gap already names its verification method in prose.
- Two budgets in config instead of one:
  `reviewing_wall` (default, diff-only) and a new `reviewing_wall_live`
  (`config/defaults.toml`, near line 53) applied only when the signal above
  fires.
  - `_ClaudeCodeInvoker.review` (`harness/claude/invoker.py:246`) needs to
    know which budget applies before it invokes, and its prompt
    (`invoker.py:263-272`) needs a second variant: the default explicitly
    tells the reviewer to judge from `git diff` + the gate's already-passing
    build/test/lint output, not to re-run servers or test suites itself
    (mirrors the reviewer.md's own existing "the validation gate already
    confirmed the build and tests pass — that is not what you're here to
    check" framing — just make it a hard instruction, not a hint); the
    live-verification variant explicitly grants the longer budget and tells
    it a live check is expected for this task.
- All three reviewer.md templates (`claude`, `ori-claude`, `claude-openrouter`)
  need the same prompt-shape change, held to byte-parity by the existing
  `test_harness_template_parity.py`.

---

## G3 — reap orphaned processes on every session end, not just forced cancellation *(implemented, 2026-09-07)*

**Problem.** `cancel_and_reap` (`proc/reap.py`) ties process-group cleanup +
the worktree-holder sweep (`proc/orphans.py`'s `sweep()`/
`find_worktree_holders()`) to `ManagedProcess.cancel()`, called only from
`_ClaudeCodeInvoker.cancel()` (`harness/claude/invoker.py:281-296`) — which
only fires on an operator cancel or the cost guard tripping. An ordinary
`error_max_turns`/timeout ending never calls it. 12 stray `vite preview`/
`http-server` processes accumulated across multiple already-`done` tasks,
drove real load average to ~17–18, and directly caused a cascading
"Axe is already running" test-flakiness failure plus an unrelated webhook
timeout — one gap silently causing others.

**No decision needed — straightforward fix.**

**Design sketch.**
- The natural hook is wherever a harness session's outcome is finalized,
  regardless of how it ended — `task/machine.py`'s `_do_implementing`/
  `_do_reviewing` (and the equivalent for `PROPOSING`) already run inside a
  `try`/`finally`-shaped flow per `timeouts.py`'s wall/stall watchdogs
  (`task/machine.py:619-644`). Add an unconditional call to
  `proc.orphans.sweep(worktree_path)` (the backstop half — no process handle
  needed, just the worktree path, which every one of these call sites
  already has) once the harness call returns, success or failure, timeout or
  not — not routed through `cancel_and_reap`/`ManagedProcess.cancel()`
  specifically, since there's no live process handle to cancel in the
  ordinary-completion case, just leftover children to sweep.
- Verify this doesn't double-sweep expensively on the already-covered forced
  -cancellation path — `sweep()` should be idempotent/cheap when there's
  nothing to find, but confirm rather than assume.

---

## G4 — structural allow-list for test-file edits, checked in real time *(implemented, 2026-09-07)*

**Problem.** `templates/harness/*/hooks/test_path_guard.py` denies every
`Edit`/`Write`/`NotebookEdit` on a protected test path unless
`allow_test_edits` was set *in advance* on the queue row
(`test_path_guard.py:77-86`) — an all-or-nothing gate with no way for a
session to request a scoped exception mid-task. On `wa-chat-chat-shell`,
blocked from the sanctioned tool for a genuinely necessary prop-signature
fix, the session fought the wall via `Bash`-based rewrites for hundreds of
turns and eventually deleted the test file outright — caught by the diff
gate, but only after the damage and the wasted turns.

**A related, independent finding from the same code read, worth folding into
this same fix:** `gate/diffgate.py:141-142` — when `allow_test_edits` *is*
set in advance (today's only escape hatch), `run_diff_gate` returns
`passed=True` immediately, skipping **all** of its checks, including
`assertion_count_decreased` and `skip_annotation_introduced`
(`diffgate.py:175-198`). That means the one existing way to grant test-edit
permission today is *more* permissive than it needs to be — a task with
`allow_test_edits=True` could weaken assertions or introduce a
`.skip(`/`@Disabled` and the diff gate would never notice, because it never
even runs the check.

**Decision (made by the user):** the structural allow-list — not a
human-approval queue.

**Design sketch.**
- The diff gate already has the exact right primitives
  (`_count_assertions`, `_is_test_path`, the skip-annotation scan) — they
  just run post-hoc, after a commit, and only against a full-file diff. The
  fix is to make the *same* structural criteria available as a **pre-flight
  check inside `test_path_guard.py`**, evaluated against the specific
  `Edit`/`Write` call being requested (its `tool_input.old_string`/
  `new_string`, or full `content` for `Write`) *before* denying it:
  - Allow when the edit doesn't reduce the file's assertion count
    (`_count_assertions` run against old vs. new content) and doesn't
    introduce a skip annotation and doesn't delete the file (`Write` with
    materially shorter content re-uses the same
    `diff_gate_loc_drop_threshold` idea, `config/defaults.toml:117`).
  - Deny (as today) anything that reduces assertions, adds a skip
    annotation, or represents a full-file deletion/replacement — exactly
    what `wa-chat-chat-shell`'s actual bad edit would still have hit.
  - This needs `_count_assertions`/the skip-annotation list factored out of
    `gate/diffgate.py` into something both the gate and the hook can import
    — the hook runs inside the harness's own sandboxed process
    (`templates/harness/*/hooks/`), so check whether `gate/` code is
    reachable from there today or whether the shared logic needs to move
    somewhere both sides can see it (likely a small shared module, not a
    duplicate copy — two copies of the same assertion-counting regex list
    drifting apart is its own future bug).
  - Fix the diff gate's own gap at the same time: `run_diff_gate` should
    still run the assertion-count/skip-annotation checks even when
    `allow_test_edits=True` — only the blanket "any modification is a
    violation" rule (lines 155-173) should be the part `allow_test_edits`
    bypasses, not the whole function.
- `allow_test_edits` stays as the escape hatch for edits the structural
  check can't safely allow (e.g. legitimately deleting an obsolete test) —
  this doesn't replace it, it narrows how often a session needs to reach for
  it.

---

## G5 — exclude gitignored paths from the gitleaks scan *(implemented, 2026-09-07)*

**Problem.** `run_gitleaks_scan` (`git/secrets.py:117-136`) deliberately
scans the raw worktree filesystem (`--no-git`) rather than just the diff —
correct in principle ("does the final state of this task's work contain a
secret," per its own docstring) — but doesn't exclude `.gitignore`d paths. A
leftover, regenerable `frontend/storybook-static/` build artifact (never
committed, left behind by an earlier session's own manual verification)
false-flagged gitleaks' `generic-api-key` rule on pure noise in Storybook's
bundled React internals (`o=e.childrens`) and blocked a fully correct task.

**No decision needed — straightforward fix.**

**Design sketch.**
- Before invoking gitleaks, compute the set of gitignored paths under the
  worktree (`git status --ignored` or `git check-ignore` over the candidate
  file list — either is a normal git plumbing call, no new dependency) and
  pass them to gitleaks as an exclusion (`--gitleaks-ignore` config or a
  `.gitleaksignore`-style path list, whichever the pinned gitleaks version
  supports cleanly) rather than trying to post-filter its JSON findings by
  path after the fact.
- At minimum, exclude common build-output directory names
  (`dist/`, `build/`, `storybook-static/`, `node_modules/`) as a static
  backstop even if the dynamic gitignore-based exclusion has edge cases —
  cheap, and covers the exact real incident even if the general case needs
  more iteration later.
- `store/failure_signature.py:42-51` already has a
  `secrets_stray_backup_artifact` signature for a related-but-different
  gitleaks false positive (a renamed `_old/` backup directory) — worth
  checking whether this fix makes that signature's underlying cause moot too,
  or whether it's a genuinely separate case that still needs its own
  handling after this fix ships.

---

## G6 — CLI flags for the resume stages that keep getting hand-run *(implemented, 2026-09-07)*

**Problem.** Every manual unblock in the wa-chat-component run —
`wa-chat-text-bubbles` (`resume_at_stage='committing'`), `wa-chat-chat-shell`
(three separate resumes), `wa-chat-storybook-vr` (three more) — required
direct `store.writer.queue_resume_at` calls (and once,
`writer.connection` raw access to flip `allow_test_edits` on an
already-queued row) instead of a documented command. `cli.main.queue_retry`
(`src/cosmo/cli/main.py:1689`) already has the `--keep-implementation` flag
as a precedent for exposing a resume-stage choice safely at the CLI layer.

**No decision needed — straightforward fix, mirrors existing code.**

**Design sketch.**
- Add `--resume-at-validating` and `--resume-at-committing` flags to
  `queue_retry`, same `Annotated[..., typer.Option(...)]` shape as
  `--keep-implementation` (`cli/main.py:1706-1709`), each setting
  `resume_at_stage` to the corresponding `TaskStatus` before calling
  `queue_retry` (`cli/main.py:1803-1853` already branches on
  `resume_at_stage`, this just adds two more ways to set it besides
  `--keep-implementation`'s `PROPOSED`).
- Both stages already work end-to-end (`resume_at=VALIDATING` shipped in the
  v19 work, `COMMITTING` since migration 9) — this is CLI surface only, no
  state-machine change.
- Worth a guard: refuse (with a clear message) combining
  `--keep-implementation` with either new flag, since they resume at
  different points and combining them is almost certainly a mistake rather
  than an intentional combination.

---

## G7 — flag a repeat rejection on the same task as its own signal *(implemented, 2026-09-07)*

**Problem.** `wa-chat-interactive-buttons-cta`'s second adversarial-review
rejection was the *same* `ButtonRow` geometry requirement its first
rejection named, defeated by a shallow CSS patch instead of the structural
fix actually requested — and nothing in the pipeline distinguished that from
two unrelated rejections, because `detect_repeat_block`
(`store/failure_signature.py:101`) only ever looks at terminal blocks
(`next_action == "block"`), and neither of these rejections was one — both
carried `next_action = "retry"` and resolved automatically.

**Decision (made by the user):** option (b) — don't try to signature-match
the freeform review-rejection text (fragile; review prose isn't a fixed
format the way `npm ci` output or an HTTP status line is). Instead, just
count *any* 2nd+ consecutive `adversarial_review` rejection on the same task
as worth flagging, regardless of whether it's textually "the same" issue as
the one before it.

**Design sketch.**
- Uses the shared consecutive-failure counter from the section above, keyed
  simply on `(task_id, failure_stage="adversarial_review")` with no
  signature classification involved — count consecutive rows regardless of
  `error_summary` content, threshold likely `2` (i.e. flag on the *second*
  rejection, don't wait for a third).
- "Flag" needs a concrete effect, not just a log line — options: (a) emit a
  new event type (`task.review_repeat_rejection` or similar) that
  `notify.watch` can route to Telegram at a higher severity than an ordinary
  retry, so a human actually sees it while the run is still going, rather
  than discovering the pattern only by reading `task_failures` after the
  fact; and/or (b) surface it in `cosmo queue show`/`cosmo report` output as
  a visible marker on the task row. (a) is more useful for an unattended
  overnight run, which is the exact scenario this whole project runs under —
  worth doing at least that much even if (b) comes later.
- Explicitly *not* in scope per the decision above: trying to determine
  whether rejection N+1 is "the same issue" as rejection N. If that turns
  out to matter later (repeated *different* rejections being common, not
  just repeated *same* ones), that's option (a) from the original
  discussion and would need the review-prompt-shaped signature work G1
  already needs for `max_turns` — revisit then, not now.

---

## G8 — classify a hard provider budget/key ceiling as its own failure type *(implemented, 2026-09-07)*

**Problem.** `classify_harness_failure` (`task/classify.py:26-49`) collapses
every non-timeout harness failure into a single
`FailureType.ENVIRONMENT_ERROR`, with `error_summary` set from
`result.output_summary` and `error_detail` always `None`. A permanent,
un-retriable condition — `wa-chat-interactive-buttons-cta`'s attempt 3
hitting `403 Key limit exceeded (total limit)` from OpenRouter — is
structurally identical to a transient one (a session-limit reset, a brief
network blip) except for free text in `error_summary` that nothing reads
programmatically. The task just keeps retrying toward `max_attempts` against
a condition retrying cannot fix.

**No decision needed — straightforward fix, reuses G1/G7's building
block.**

**Design sketch.**
- `store/failure_signature.py`'s existing substring-matching taxonomy
  (`_SIGNATURES`, `store/failure_signature.py:34-59`) is the right place for
  this — add a `provider_budget_exceeded` (or similarly named) signature
  matching on `("Key limit exceeded",)` (and, if found, the equivalent
  Anthropic/native-harness phrasing for a monthly-spend-limit message,
  already seen verbatim in this same run's `wa-chat-template-resolver`
  failure: `"You've hit your monthly spend limit"`) — but note
  `classify_failure_signature` currently matches against `error_detail`,
  which `classify_harness_failure` never populates
  (`task/classify.py:44-49` leaves it `None`); this needs `error_summary`
  fed into the classifier too, or a second small classifier that matches on
  summary text specifically for this environment-error path.
- Once classified, the retry loop (`task/machine.py`) can treat this
  signature as immediately terminal (`will_retry = False`, straight to
  `blocked`) rather than spending the task's remaining `max_attempts` on a
  call that cannot succeed — mirrors how `environment_error` already skips
  the ordinary in-run retry treatment in some paths (`task/machine.py:155`'s
  own comment about `environment_error` never getting an in-run retry at
  `COMMITTING`/`MERGING`) but needs the same posture applied here.
- The blocked message should say what a human needs to go do ("raise the
  OpenRouter key budget," "raise the monthly Claude spend limit") rather
  than just repeating the provider's own error text, since that's the whole
  point of distinguishing this case from an ordinary retryable blip.

---

## G9 — reviewer verdict lost on cwd drift *(fixed, 2026-09-04)*

Already shipped — recorded here only for completeness against the numbering
above, not as open work. `task/review.py`'s `read_review_verdict` only ever
read `.cosmo/review-result.json` from the worktree root; a review session
that `cd`'d into `frontend/` before writing its verdict left it undiscovered,
discarding a genuine `"approved"` result and forcing a wasted
re-`IMPLEMENTING` pass on `wa-chat-text-bubbles`. Fixed by
`_find_stray_verdict_file` (a bounded structural fallback search under the
worktree, skipping `node_modules`/`.git`/`dist`/`build`/`target`) plus an
explicit cwd-drift warning added to all three reviewer templates. See
`docs/handoff.md`'s v17 entry and `docs/v3-implementation-state.md`'s
cumulative deviations table for the full account.

## Non-goals for this document

- **Not attempting a general "any harness error gets a structured type"
  refactor.** G8 adds one specific signature for one specific, real,
  recurring condition — not a full taxonomy of every possible provider
  error. `classify_harness_failure`'s doc comment explains why that
  distinction (code-quality problems only observable at `VALIDATING`) is
  deliberate; this plan doesn't revisit it.
- **Not building a human-approval workflow for test edits.** The user
  explicitly chose the structural allow-list over this for G4; a
  human-in-the-loop escalation path for edits the structural check can't
  safely allow is out of scope here, not merely deferred.
- **Not re-litigating `allow_test_edits` as a concept.** G4 narrows how
  often it's needed and closes a real gap in what it bypasses; it doesn't
  remove the flag or change who can set it (still pre-set on the queue row,
  now also settable via G6's resume flags in the one case that already
  applied — flipping it by hand mid-block).
- **Not tuning the actual numeric values** (growth factors, thresholds,
  budget seconds) as part of this planning pass — every number sketched
  above (`1.5`, `3.0`, a rejection threshold of `2`) is a starting point to
  implement and observe, not a conclusion reached from data; retune after
  real runs the way `docs/v3-cosmo-autonomous-agent-spec.md`'s own timeout
  values are already flagged for (`config/defaults.toml:44-45`'s comment on
  `[timeouts]`).

## Testing expectations

Each item needs the same rigor the rest of this codebase already holds
itself to (`docs/handoff.md`'s "Conventions" section): a fake-harness/
fake-gate unit test reproducing the real failure shape found in this audit
before the fix, not just a test of the new code in isolation — e.g. G3
should have a test asserting a worktree-holder process actually gets swept
after a *simulated* `error_max_turns` ending, not only after an explicit
`cancel()`; G4 should have a test where a `FakeHarnessAdapter`-driven edit
that only changes a JSX prop value passes the pre-flight check while one
that removes an `expect(...)` line is denied, mirroring
`gate/diffgate.py`'s own existing test shapes. `./check.sh` must stay green
throughout; each fix gets its own entry in
`docs/v3-implementation-state.md`'s cumulative deviations table (next number
**90**) once actually implemented, per this repo's normal "when you finish"
checklist in `docs/handoff.md`.
