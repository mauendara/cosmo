# Use the Codex harness

Cosmo can run its propose, implement, and review stages through the
non-interactive Codex CLI. The integration was validated with `codex-cli
0.153.0`; re-run the probe after upgrading Codex because its flags, event
schema, and sandbox behavior may change.

## Install, authenticate, and select Codex

Install Codex, authenticate it interactively with the account whose included
usage you intend to use, and verify the login before starting Cosmo:

```bash
codex login
codex --version
cosmo harness probe --harness codex --prompt "Reply briefly that the probe works."
```

Cosmo preserves Codex's saved authentication in `CODEX_HOME`, but invokes each
stage with personal behavior configuration ignored. Personal hooks, MCP
servers, apps, plugins, and instructions therefore cannot silently alter an
unattended run.

Select Codex during bootstrap or for an individual command:

```bash
cosmo init ~/code/my-app --harness codex --project-template _blank
cosmo doctor --harness codex --project-path ~/code/my-app
cosmo run --harness codex --repo ~/code/my-app
```

Bootstrap installs policy and hooks under `.agent/codex` and exposes skills at
`.agents/skills`. It deliberately does not create `.codex`: a real 0.153.0
workspace-write run rejected that root symlink as an unsafe sandbox mount.
Hooks are injected explicitly instead.

## Configure a Codex model

The shipped global model is a Claude model id, so configure a model available
to your Codex account. Keep it in the per-harness table so switching harnesses
does not require editing the global defaults:

```toml
[harness.overrides.codex]
model = "gpt-5.6-sol"
# Optional role-specific choices:
# propose_model = "..."
# implement_model = "..."
# review_model = "..."
```

The exact model names available to an account can change. Confirm the selected
name with `cosmo harness probe --harness codex` before an unattended run.
Codex supports only Cosmo's `permission_mode = "dontAsk"`; preflight rejects
other modes rather than weakening the sandbox.

## Billing, cost, and quota limits

This adapter is intentionally for saved-login execution. Unset
`CODEX_API_KEY`: `cosmo doctor --harness codex` fails when it is present so an
API key cannot silently switch the run to separately billed API usage. An
explicit API-billed Codex mode is not supported.

In the validated JSONL stream, Codex reported input, cached-input, output, and
reasoning tokens, but no authoritative USD cost, quota window, or reset time.
Consequently:

- `reports_native_cost` is false, and Cosmo cannot enforce its USD ceilings
  from Codex-native spend data.
- Codex subscription allowance and any external billing limit must be managed
  in the Codex account.
- Quota detection can only fall back to Cosmo's terminal-error and timing
  heuristics; it cannot identify a weekly window from Codex's stream.

Treat token totals in raw harness logs as usage evidence, not as a dollar
ledger.

## Safety boundary and residual limitations

Codex runs with `workspace-write`, no interactive approvals, fresh ephemeral
sessions, explicitly injected Cosmo hooks, and unnecessary hosted features
disabled. Real hostile validation confirmed denials for protected-test edits,
forbidden annotations, destructive Git and push commands, detached background
work, review source writes, and known secret-file reads. A write outside the
task worktree was also rejected by Codex's sandbox. Cosmo therefore reports
`supports_gating = true` for this validated profile.

The hooks are defense in depth, not a confidentiality boundary. They recognize
known dangerous operations; the Codex sandbox is what contains filesystem
writes. Do not add hosted tools or use Codex's unrestricted sandbox bypass
without repeating the adversarial validation.

Codex's sandbox also protects the linked worktree's Git metadata. During
`IMPLEMENTING`, Codex edits files but does not stage or commit them. After a
successful call, Cosmo stages the implementation (excluding `.agent`,
`.agents`, and `.cosmo`) and creates the task commit before validation. This
is expected behavior, not a missing permission.

Official Codex references: [non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[hooks](https://learn.chatgpt.com/docs/hooks),
[configuration](https://learn.chatgpt.com/docs/config-file/config-reference),
and [skills](https://learn.chatgpt.com/docs/build-skills).
