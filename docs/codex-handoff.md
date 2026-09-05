# Codex adapter worktree handoff

Last updated: 2026-09-05
Status: Phase 0 contract spike in progress; production implementation has not started

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
- No production Codex adapter implementation has been made.
- The implementation plan is `docs/c1-codex-adapter-plan.md`.
- Phase 0 findings are recorded in `docs/codex-phase0-findings.md`.
- Sanitized and deliberately derived JSONL fixtures are under
  `tests/fixtures/codex_jsonl/`.
- The Phase 0 work was checked with `./check.sh`: 671 passed and 9 skipped.
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
- Install the template at `.agent/codex` and expose `.codex` plus
  `.agents/skills` through collision-safe symlinks.
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

## Next implementation task

Finish the two Phase 0 observations this host could not prove, then begin Phase
1 parser tests and implementation:

1. On a clean host that is not already inside a Codex sandbox, capture a
   successful command event and a successful `apply_patch` event under the
   exact workspace-write policy.
2. Adversarially verify that `--dangerously-bypass-hook-trust` plus explicit
   audited hook injection does not weaken the effective execution sandbox,
   despite the observed hook payload value.
3. Add parser tests around `tests/fixtures/codex_jsonl/`, including arbitrary
   byte chunking, malformed lines, and a truncated final line.
4. Implement deterministic argv/environment construction using
   `--ignore-user-config` and explicit audited hook injection. Do not rely on
   project hook discovery.
5. Keep `supports_gating=False` until the clean-host adversarial gate passes.

The current session itself is nested inside a Codex sandbox. Its bubblewrap
registry is mounted read-only, and the deprecated Landlock fallback also
failed writes. Do not work around this with the forbidden unrestricted
approval-and-sandbox bypass. See `docs/codex-phase0-findings.md` for commands,
event shapes, spend accounting, and the exact limitation.

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

## Repository context at planning time

The main handoff reported v19 as the latest completed work, with `./check.sh`
clean at 671 passing tests and 9 skipped tests. It identified deviation 90 as
the next available implementation-state entry. Reconfirm both facts before
using them because the source branch may evolve independently after this
worktree was created.
