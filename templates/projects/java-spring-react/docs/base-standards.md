# Base Standards

## Code style

- **Backend**: formatted via Spotless (or an equivalent), enforced in the
  build -- a formatting violation fails the build stage, not just CI lint.
- **Frontend**: Prettier + ESLint (`typescript-eslint`), same posture --
  enforced, not advisory.
- Neither is Cosmo's concern to configure by hand each run: `cosmo init`
  seeds this file, but the actual formatter/linter config
  (`.editorconfig`, `eslint.config.*`, Spotless Gradle/Maven config) lives
  in the target repo and is set up once, by a human, early in the project.

## Testing expectations

- Every backend service method with a non-trivial branch gets a unit test;
  every new endpoint gets at least one integration test exercising it
  through the real Spring context.
- User-facing flows (not every component in isolation) get Playwright e2e
  coverage -- an e2e suite that only re-tests what unit tests already cover
  is slow for no benefit.
- A bug fix ships with a regression test reproducing the bug, added before
  the fix, so the test would have failed without it.

## Review checklist

Beyond "the validation gate passed" (spec: the gate is the only source of
truth about correctness, but passing doesn't mean a change was reviewed for
these):

- Does the change match its OpenSpec proposal's stated scope, or did it
  grow beyond it?
- Are new endpoints' error responses following `backend/error-handling.md`?
- Does anything here in `docs/` need updating as a result of this change?
- Is there a secret, credential, or internal id anywhere in the diff that
  shouldn't be there?
