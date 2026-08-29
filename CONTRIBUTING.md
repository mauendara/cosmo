# Contributing

Thanks for looking. Two contributions are especially wanted:

- **A harness adapter** for another coding agent — Codex CLI, OpenCode,
  anything with a CLI. The interface is stable, documented, and
  test-enforced: [write-a-new-adapter](user-docs/how-to/write-a-new-adapter.md).
- **A project template** for a stack the shipped ones don't cover:
  [add-project-template](user-docs/how-to/add-project-template.md).

## Setup

```bash
git clone <this repo> cosmo && cd cosmo
uv sync
uv tool install --editable .   # templates are read from the checkout, not the wheel
```

Everything runs through one script:

```bash
./check.sh
```

which is `ruff check`, `ruff format --check`, `mypy` (strict, over `src` and
`tests`), and `pytest`. **Run it before every push.** CI runs the same thing;
there is nothing it checks that this doesn't.

Individual pieces, when iterating:

```bash
uv run pytest tests/test_gate_diffgate.py -q
uv run pytest -q -k quarantine
uv run ruff format .
uv run mypy
```

## What the code expects of you

`mypy` runs in strict mode over both `src` and `tests`. `ruff` is configured
with `E`, `F`, `I`, `UP`, `B`, `SIM` at a 100-character line length. Neither
is negotiable per-PR; if a rule is genuinely wrong for a case, say so in the
PR rather than adding a blanket ignore.

Beyond the linters, three conventions carry real weight in this codebase:

**Comments explain *why*, and cite evidence.** The existing code is dense
with comments recording what a decision cost to learn — a real failure, a
verified-by-hand observation, a rejected alternative and the reason. That's
deliberate: the expensive knowledge in an unattended system is the knowledge
of what breaks. If you fix something found by running it for real, say so in
the comment.

**Fail closed and fail loud.** A missing `gitleaks` blocks the commit rather
than skipping the scan. An expired quarantine entry breaks the file rather
than being ignored. An unknown config key is an error rather than a silent
no-op. When in doubt, refuse and explain.

**Never trust the model's prose.** Structured output the *tool* emits is
data. A sentence the *model* wrote is not a signal — not for success, not for
a verdict, not for classification. The review verdict goes through a JSON
file for exactly this reason.

## Architectural boundaries

Four boundaries are enforced by tests that parse the source. Breaking one
fails the build, not review:

| Boundary | Enforced by |
| --- | --- |
| `cosmo.gate` never imports `cosmo.harness` | `tests/test_gate_boundary.py` |
| `cosmo.git` never imports `cosmo.harness` | `tests/test_git_boundary.py` |
| Only the Claude adapter module names Claude-specific binaries, flags or env vars | `tests/test_harness_boundary.py` |
| `--dangerously-skip-permissions` / `bypassPermissions` never appear in constructed argv | adapter assertion plus an external test |

These make guarantees structural rather than conventional. The gate can't be
influenced by the agent because there's no code path from one to the other. A
merge conflict is never handed back to the agent to resolve blind because
there's no adapter in scope for that code to reach.

If you need to cross one of these, that's a design conversation before it's a
patch.

Similarly, `cosmo.doctor` is harness-agnostic by construction — harness
preconditions come from calling `preflight()` on the resolved adapter, never
from core code knowing what they are.

## Testing

Real external processes are never invoked from a unit test. The established
pattern is to fake the process and test the mechanics:

- `FakeHarnessAdapter` (`cosmo.harness.fake`) — scriptable harness outcomes,
  a first-class adapter rather than a test fixture, so state-machine tests
  can target it directly.
- `tests/fixtures/` — shell-script stand-ins for `docker` and friends,
  injected via a `*_bin` parameter.

Adapters take their binary path as an injectable keyword argument for this
reason. New code that shells out should do the same.

Two kinds of test are worth extra care because their failure modes are
expensive and invisible:

- **Process-group kills.** Spawn a fixture that forks a child ignoring
  SIGTERM, cancel it, assert the whole group is gone. A leaked process pool
  poisons every later task, hours after the run that spawned it.
- **Boundary tests.** If you add a module that could plausibly reach across
  one of the boundaries above, extend the corresponding test.

## Commits and AI attribution

**Do not attribute commits to an AI as author or co-author.** No
`Co-Authored-By` trailer naming a model or tool, no `Assisted-by`-style
trailer, no AI name or address in the author or committer fields. Git
authorship identifies the person accountable for the change, and that's a
human — the one who reviewed it and is answering questions about it.

**Do disclose AI assistance**, in the PR description or the commit body.
Describe it in prose: what the assistance covered, and what you verified
yourself. Something like "drafted the parser with an assistant; I wrote the
fixture cases and verified the counts against a real Playwright report by
hand" is exactly right. There's no required trailer or syntax — a sentence
someone can read is the point, not a machine-parseable token.

The distinction is simple: attribution is about accountability, disclosure is
about transparency. Substantial parts of this project were AI-assisted and
say so; none of it is signed by a model.

## Pull requests

- **Branch from `develop`.** That's the integration branch. `main`/`master`
  is promoted by hand.
- **One concern per PR.** A boundary change and a feature in the same diff is
  two reviews wearing one hat.
- **`./check.sh` passes.** All of it.
- **Say what you verified for real.** This project's most valuable
  commits came from watching a real run fail and fixing the actual cause.
  If you did that, the PR description is where it belongs. If you only
  reasoned about it, say that too — it's useful information, not a
  confession.
- **New config keys need a default, a validator if a bad value is
  dangerous, and a row in
  [config-schema](user-docs/reference/config-schema.md).**
- **New events need a payload table in
  [event-schema](user-docs/reference/event-schema.md).**
- **New commands or flags need a row in [cli](user-docs/reference/cli.md).**

## Adding an adapter

Follow [write-a-new-adapter](user-docs/how-to/write-a-new-adapter.md) — it
carries the full contract and a checklist. Beyond it, for the PR:

- Declare capabilities **honestly**. `supports_gating=True` for a harness
  that can't actually deny a tool call pre-execution means Cosmo believes it
  has prevention it doesn't have.
- Add a boundary test asserting nothing outside your module names your
  binary or environment variables.
- Include the output of `cosmo harness probe --harness <yours>` against a
  real installation, and ideally one real task driven end to end.

## Adding a project template

Follow [add-project-template](user-docs/how-to/add-project-template.md). In
the PR, say what stack it targets and what `[gate]` configuration it needs.

The templates worth having are the ones written from real failures — the
Playwright version pin and reporter path in `vite-react-local` each exist
because a real task burned an attempt discovering them. Two sentences that
save a failed attempt per project are worth more than a page of general
advice.

## Reporting bugs

Include:

```bash
cosmo --version
cosmo doctor
cosmo config show
cosmo report --run <run_id>
cosmo queue failures <task_id>
cosmo events tail --run <run_id> --payload --limit 200
```

**Redact first.** Payloads and failure detail can carry paths, branch names,
error text from your source, and file contents.

For a security issue, do **not** open a public issue — see
[SECURITY.md](SECURITY.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE). By contributing, you agree that
your contributions are licensed under the same terms.
