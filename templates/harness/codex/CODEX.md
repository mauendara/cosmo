# Operating policy — Cosmo-managed repository

Cosmo invokes Codex headlessly once per workflow stage. You cannot ask a
clarifying question or rely on a later continuation. Make the smallest
reasonable in-scope choice, record durable decisions in the project docs, and
finish all foreground work before returning.

The validation gate and Git diff are the sources of truth. Model prose,
checkboxes, and a successful Codex process do not prove correctness. Never
weaken tests or claim success early to influence the orchestration.

## OpenSpec

Use the exact change id supplied in the prompt. Inspect its state before acting:

```text
openspec status --change <change-id>
openspec instructions <artifact> --change <change-id>
```

During implementation, work through `tasks.md` and check a literal
`- [ ] N.M Description` item only after that subtask is complete. Run
`openspec validate <change-id>` before finishing proposal artifacts.

## Guardrails

Cosmo injects audited `PreToolUse` hooks from `.agent/codex/hooks`; `hooks.json`
documents the same set but invocation-time injection is authoritative because
personal configuration is ignored. Do not edit `.agent/codex` or
`.agents/skills`: they are regenerated from Cosmo's template.

- Test paths under `src/test/**`, `e2e/**`, and JS/TS `*.spec.*` / `*.test.*`
  are write-protected unless the queued task explicitly permits test edits.
- Introducing `@Disabled`, `@Ignore`, `test.skip`, `it.skip`,
  `describe.skip`, or `xit(...)` is denied.
- `git push`, destructive reset/clean operations, and commits using
  `--no-verify` are denied. Do not stage or commit from Codex: the workspace
  sandbox protects linked-worktree Git metadata, so Cosmo stages and commits
  completed work after the implementation call. Cosmo also owns push and merge.
- Background execution (`&`, `nohup`, `disown`, or a background tool option)
  is denied. Run commands synchronously.
- Reads of `.env*`, `secrets/**`, private-key files, and `id_rsa*` are denied.
- In review, repository writes are denied except for the exact root path
  `.cosmo/review-result.json`. Review the diff without fixing it.

Repository-owned `AGENTS.md` instructions remain in force. Never replace or
rewrite that file as part of Cosmo setup or ordinary task work. Stay inside the
current worktree: do not search parent or sibling directories for additional
instruction files.

## Project knowledge and commits

Read the relevant files under `docs/` before editing. Add only durable
architecture or convention decisions there, not a narration of the run. Pin
dependencies to versions compatible with the repository's documented toolchain
and commit generated lockfiles.

Do not add a model-naming `Co-Authored-By` or `Assisted-by` trailer. Follow the
repository's own contribution rules for any required prose disclosure.
