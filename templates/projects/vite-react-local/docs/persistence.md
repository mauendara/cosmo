# Persistence

There is no server and no database. Every persisted entity lives in the
browser's `localStorage`, accessed through a single small `useLocalStorage`
hook in `src/hooks/` -- no other code in the app calls
`localStorage.getItem`/`setItem` directly, so the read/write/error-handling
contract below lives in exactly one place.

## Key naming

`<app-slug>:<entity>:v<n>` -- e.g. `habit-tracker:habits:v1`,
`memory-game:best-scores:v1`. The app slug namespaces keys so this app's
data can't collide with another `vite-react-local` app opened from the same
origin during local development; the version suffix is what
`useLocalStorage` checks on read (below).

## Versioning: wipe and reinitialize on mismatch

If a stored value's shape doesn't match what the current code expects (the
version suffix in its key is stale, or the parsed JSON fails a basic shape
check), discard it and reinitialize with the entity's default state. Do not
write migration logic to upgrade an old shape in place.

This is deliberate, not a shortcut taken under time pressure: this template
is a disposable testbed, not an app with real user data to protect, and
over-engineering a migration path here would be solving a problem this app
doesn't have. If a task ever needs to change a persisted entity's shape,
bump its key's version suffix and let the wipe-and-reinitialize path handle
the rest.

## Read/write contract (`useLocalStorage`)

- **Read**: `JSON.parse` wrapped in `try/catch`. A parse failure, a missing
  key, or a version mismatch (above) all fall back to the entity's default
  state -- they are not distinguished from each other, and none of them
  throw out of the hook.
- **Write**: `JSON.stringify` wrapped in `try/catch`, specifically watching
  for `QuotaExceededError` (thrown when `localStorage` is full or disabled,
  e.g. Safari private browsing). On that error, surface it to the user via
  whatever the app's existing error-display convention is -- do not let it
  propagate as an unhandled exception, and do not silently drop the write
  without telling the user their data didn't save.
- Every write is a full replace of that key's value (`JSON.stringify` the
  whole current entity state), not a partial patch -- there is no
  transactional guarantee across multiple keys, so a feature that touches
  more than one entity should be written to tolerate one succeeding and the
  other failing (rare in practice for `localStorage`, but the `try/catch` at
  each call site is what actually protects against it, not an assumption
  that both writes happen atomically together).
