# Codex adapter worktree handoff

Last updated: 2026-09-06
Status: Complete — Phases 0-6 and real lifecycle validation passed

## Mandatory operating directives

These directives apply to every future session working on the Codex adapter:

1. Work only in `/home/dev/delta/codex-adapter`.
2. Never modify `/home/dev/delta/cosmo`, the original Cosmo CLI checkout.
3. Keep all Codex adapter work on the `codex-adapter` branch and its dedicated
   worktree.
4. Never run `git push`. The user has explicitly prohibited pushing this work.
5. Do not delete, reset, clean, or overwrite unrelated user changes.
6. Before editing, verify both the current directory and branch:

   ```text
   pwd
   git branch --show-current
   git status --short --branch
   ```

   Expected directory: `/home/dev/delta/codex-adapter`
   Expected branch: `codex-adapter`
7. Follow the repository root `AGENTS.md` and `CONTRIBUTING.md` in full.
8. Run `./check.sh` before considering implementation complete.
9. Commit messages must follow the repository's AI-attribution policy: never
   add a model-naming `Co-Authored-By` or `Assisted-by` trailer.

If the required worktree is missing, on the wrong branch, or contains changes
that cannot be safely preserved, stop and ask the user rather than falling back
to the original checkout.

## Current state

- Branch: `codex-adapter`
- Worktree: `/home/dev/delta/codex-adapter`
- Base commit when the worktree was created: `2933ea1`
- The original `/home/dev/delta/cosmo` checkout was on branch `private` and is
  reserved for another agent.
- The production parser, invocation mechanics, adapter, template, bootstrap,
  guardrails, public documentation, and release evidence are complete;
  `codex` is registered and visible through the existing harness-agnostic CLI
  surfaces.
- The implementation plan is `docs/c1-codex-adapter-plan.md`.
- Phase 0 findings are recorded in `docs/codex-phase0-findings.md`.
- Sanitized and deliberately derived JSONL fixtures are under
  `tests/fixtures/codex_jsonl/`.
- The Phase 2 work was checked with `./check.sh`: 714 passed and 9 skipped.
- Never push, including after tests pass or commits are created.

Read the repository's main `docs/handoff.md` as historical context, but treat
this handoff as the operational authority for the separate Codex worktree.

## Goal

Implement a first-class `codex` harness and repository template so Cosmo can
use the non-interactive Codex CLI for propose, implement, and review stages
while preserving Cosmo's existing adapter boundaries, structured-signal rules,
process lifecycle guarantees, and safety posture.

## Research already completed

The planning session read the existing adapter guide and inspected the Claude
adapter, stream parser, configuration, template synchronization, symlink
bootstrap, hook tests, and boundary tests.

Official Codex documentation consulted:

- Non-interactive mode: <https://learn.chatgpt.com/docs/non-interactive-mode>
- Hooks: <https://learn.chatgpt.com/docs/hooks>
- Configuration: <https://learn.chatgpt.com/docs/config-file/config-reference>
- Skills: <https://learn.chatgpt.com/docs/build-skills>

Observed installed CLI during planning: `codex-cli 0.153.0`.

Important findings:

- `codex exec --json` emits JSONL with thread, turn, item, and error events.
- The documented default is a read-only sandbox; implementation requires an
  explicit `workspace-write` sandbox.
- `--ignore-user-config` preserves authentication from `CODEX_HOME`, but the
  real 0.153.0 spike found that it also suppresses project hooks.
- `--ephemeral` fits Cosmo's fresh-call model and avoids relying on persisted
  rollout state.
- Codex exposes token usage but not an authoritative USD cost in the observed
  terminal event shape.
- Project hooks require trust. The hook-trust bypass makes an audited
  symlinked project hook run, but the hook payload unexpectedly reports
  `permission_mode="bypassPermissions"`; its effective sandbox scope therefore
  remains an explicit validation item.
- Codex hooks are not a complete enforcement boundary: hosted and specialized
  tools may bypass them. Nonessential tools must be disabled.
- Project skills are discovered under `.agents/skills`.
- Codex file edits commonly use `apply_patch` with patch text rather than the
  structured `Edit`/`Write` payloads used by Claude.

## Key implementation decisions

- Add `src/cosmo/harness/codex/{adapter,invoker,stream}.py` and register the
  harness as `codex`.
- Use a fresh `codex exec --json` process for every Cosmo role.
- Use `ManagedProcess` for process groups, streaming, cancellation, and reaping.
- Treat exit code zero as the only success condition.
- Never parse model-authored prose for success, review, quota, or retry signals.
- Preserve Git as the source of truth for changed files.
- Report native cost as unsupported until an authoritative structured USD value
  exists.
- Start with `supports_gating=False`; enable it only after real adversarial
  validation proves the bounded tool surface and hooks.
- Install the template at `.agent/codex` and expose `.agents/skills` through a
  collision-safe symlink. Do not recreate the incompatible `.codex` symlink.
- Never overwrite the user's root `AGENTS.md`. Inject a short developer
  instruction that points Codex at `.agent/codex/CODEX.md`.
- Adapt hooks to Codex's `apply_patch` and Bash payloads rather than copying the
  Claude hooks byte-for-byte.
- Preserve normal Codex authentication, isolate personal behavior settings,
  and scrub `CODEX_API_KEY` unless an explicit API-billed mode is designed.
- Disable multi-agent execution, apps, plugins, browser/computer tools, web
  search, and any unnecessary hosted tools.
- Never use the unrestricted approval-and-sandbox bypass flag.

## Phase 0 results

The first Phase 0 pass ran against the real `codex-cli 0.153.0` in disposable
Git repositories and stayed within a three-authenticated-turn ceiling. It
confirmed:

- Exit-0 `turn.completed` and exit-1 `turn.failed` JSONL shapes.
- Token usage without an authoritative USD-cost field.
- A failed `apply_patch` attempt represented as `file_change` item events.
- `.codex -> .agent/codex` hook discovery when hook trust is bypassed.
- A real `PreToolUse` Bash denial before the target file was written; the
  denial text appeared on stderr rather than as a command item in JSONL.
- Saved authentication surviving `--ignore-user-config`, while a deliberately
  invalid personal config was excluded.
- Project hooks being suppressed by `--ignore-user-config`, for both a real
  `.codex` directory and the proposed symlink.
- Explicit `-c` hook injection from `.agent/codex/hooks/` working while
  `--ignore-user-config` remains active.
- Cancellation of an invocation whose injected SessionStart hook waited on a
  SIGTERM-ignoring grandchild; no delayed survival marker appeared.

The disposable repositories and temporary authentication symlink were removed.
No Cosmo command, queue, database, credential file, target repository, or
unrelated worktree was modified.

## Phase 1 completed

- `stream.py` incrementally parses arbitrary byte chunks, tolerates malformed
  and truncated records, captures the thread and terminal state, deduplicates
  tool item lifecycles by item id, and describes activity only from structured
  command/path fields.
- `CodexInvoker` constructs a deterministic `codex exec --json` invocation
  with workspace-write sandboxing, non-interactive approvals, personal-config
  isolation, disabled hosted/agent features, explicit audited hook injection,
  and the unrestricted approval-and-sandbox bypass structurally absent.
- The child environment preserves `CODEX_HOME`, scrubs `CODEX_API_KEY`, and
  supplies the task, database, and role variables hooks need.
- `ManagedProcess` now optionally preserves stderr in a separate sidecar. Codex
  stdout stays at the `HarnessResult.raw_log_path` (`*.ndjson`) and hook/error
  stderr is retained beside it as `*.stderr`; existing callers retain their
  prior combined-log behavior when they do not request a sidecar.
- The fake Codex executable covers success, nonzero exits, malformed output,
  stderr retention, hangs, and a SIGTERM-ignoring descendant. No real Codex
  model call was made during Phase 1.
- The installed `codex-cli 0.153.0` accepted the selected feature toggles and
  inline `hooks.PreToolUse` TOML in a no-model-call config parse.

## Phase 2 completed

- `CodexAdapter` declares the conservative initial capability set, including
  `supports_gating=False` and `reports_native_cost=False`.
- Cheap preflight checks only the executable, rejects `CODEX_API_KEY`, and
  fails closed for permission modes other than the explicitly mapped
  non-interactive `dontAsk` mode. It performs no authentication or model call.
- Probe, propose, implement, and review resolve the correct role models and
  delegate to one fresh invoker call with an explicit role environment value.
- Propose pins the exact OpenSpec change id; implement always inspects existing
  worktree state and appends retry evidence; the review prompt authorizes only
  the canonical absolute verdict path and never resumes an implementation
  session. Phase 3 supplies the enforcement hook behind that instruction.
- Progress uses the existing core `tasks.md` fallback and cancellation delegates
  to the invoker's process-group lifecycle implementation.
- Registry, CLI listing, and architectural boundary tests cover `codex` without
  adding harness-specific branches to core orchestration.
- `./check.sh` passed outside the nested sandbox: ruff and formatting clean,
  mypy clean across 177 source files, and 714 tests passed with 9 skipped. The
  sandboxed run passed 708 tests but its six pre-existing socket-dependent tests
  could not create sockets; the unrestricted-socket rerun passed all of them.

## Phases 3 and 4 completed

- `templates/harness/codex/` contains `CODEX.md`, a documented hook set, six
  audited hook scripts plus their shared library, and Codex-discoverable
  OpenSpec/spec-enrichment skills.
- Bootstrap exposes `.agents/skills -> ../.agent/codex/skills` with a relative
  link. It creates a real nested `.agents` parent, preserves real-path
  collisions, does not replace non-Cosmo symlinks, and never traverses a
  symlinked parent. Phase 5 removed the earlier `.codex` link after the real
  sandbox rejected it.
- Codex hooks parse all affected `apply_patch` headers, examine only added
  patch lines for forbidden annotations, and cover shell mutations of protected
  tests, destructive Git operations, detached work, review writes, and secret
  reads.
- Review mode permits repository reads and only the canonical root verdict
  write. Guardrails remain defense in depth; Phase 5's clean-host adversarial
  validation subsequently justified `supports_gating=True`.
- `./check.sh` passed outside the nested sandbox: Ruff and formatting clean,
  mypy clean across 178 source files, and 741 tests passed with 9 skipped.

## Phases 5 and 6 completed

Real validation on 2026-09-06 exercised the exact adapter, successful command
and patch events, every hostile guardrail, malicious personal configuration,
process-tree cancellation, fresh review isolation, and one complete disposable
Cosmo lifecycle. The hostile run proved both hook denials and independent
workspace-write containment, so `supports_gating=True` is now honest for the
validated tool profile.

The real runs corrected four assumptions: `.codex -> .agent/codex` is rejected
by the 0.153.0 sandbox and is no longer bootstrapped; a composed invoker must
rebind its `cwd` when orchestration changes the adapter's worktree; read-only
`sed` parsing must inspect option tokens rather than match arbitrary letters;
and Codex cannot write linked-worktree Git metadata. After a successful
implementation, Cosmo now commits pending source output itself while excluding
managed `.agent`, `.agents`, and `.cosmo` paths. This remains a no-op for
self-committing harnesses.

The decisive lifecycle reached every state through `DONE`, merged the exact
`Hello from Codex.` file, left `develop` clean, and archived/promoted the
OpenSpec change. English and Spanish user docs cover selection, saved-login
authentication, per-harness model configuration, the unsupported API-billed
mode, token-only accounting, quota degradation, guardrails, and authoring
lessons. Detailed evidence is in `docs/codex-phase0-findings.md` and
`docs/v8-validations-for-later.md`.

No implementation task remains. Run `./check.sh` after any further change and
never push this branch. Final verification on 2026-09-06: `./check.sh` exited
0; Ruff and formatting passed, mypy found no issues in 178 source files, and
pytest reported 755 passed and 9 skipped in 90.66 seconds.

## Expected implementation areas

Likely changes are limited to:

- `src/cosmo/harness/codex/`
- `src/cosmo/harness/registry.py`
- Harness/config integration needed to select `codex`
- `src/cosmo/bootstrap/symlinks.py`
- `templates/harness/codex/`
- Codex-specific fixtures and tests, plus existing registry/bootstrap/boundary
  tests
- English and Spanish user documentation
- Implementation-state and validation-tracking documents

Avoid broad core refactors unless an existing contract demonstrably cannot
represent required Codex behavior.

## Validation expectations

Automated tests must cover parser chunking and malformed lines, structured-only
classification, exact argv/environment behavior, model roles, retry context,
raw logs, cancellation and grandchildren, bootstrap idempotency, path
collisions, patch and Bash guardrails, review isolation, registry/CLI exposure,
and architectural boundaries.

Real validation must cover the exact adapter invocation, hostile guardrail
attempts, personal-config isolation, cancellation, a fresh review, and a full
propose-to-finish Cosmo lifecycle in a disposable repository.

At completion, run `./check.sh` and record its exact result in the appropriate
state document. Do not push the branch.

Phase 1 verification: `./check.sh` exited 0 on 2026-09-05. Ruff check and
format check passed, mypy found no issues in 175 source files, and pytest
reported 701 passed and 9 skipped in 80.77 seconds. The sandboxed attempt could
not bind sockets used by unrelated existing tests; the approved full test run
outside that socket restriction passed.

## Repository context at planning time

The main handoff reported v19 as the latest completed work, with `./check.sh`
clean at 671 passing tests and 9 skipped tests. It identified deviation 90 as
the next available implementation-state entry. Reconfirm both facts before
using them because the source branch may evolve independently after this
worktree was created.
