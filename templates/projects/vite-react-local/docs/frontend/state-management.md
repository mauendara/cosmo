# Frontend State Management

## Default to local state

`useState`/`useReducer` inside a custom hook (see `architecture.md`) is the
default and, for most tasks in this template, the entire answer. There is no
server state here -- nothing is fetched, cached, or invalidated -- so the
usual local-vs-server-state split that a networked app needs doesn't apply.

## No state management library by default

Do not introduce Redux, Zustand, Jotai, or similar unless a specific task
explicitly justifies it. A `useReducer` + a couple of custom hooks is
sufficient for a single-user, single-tab CRUD or game app with no
cross-feature state to coordinate; adding a state library here is scope the
task didn't ask for, and an unnecessary dependency on top of that -- prefer
the simplest thing that satisfies the task at hand over anticipating
requirements the app doesn't have yet.

## Conventions

- One hook owns one piece of domain state end-to-end (reads from
  `localStorage` on mount, exposes actions, writes back on change) -- see
  `../persistence.md` for the read/write contract itself.
- Derived values (a filtered todo list, a computed streak, a running total)
  are computed at render time from the source state, not stored redundantly
  alongside it -- a stored derived value is a second place that can silently
  go stale, and there's no performance case here that justifies memoizing
  ahead of an actual measured problem.
- If two components that aren't in a direct parent/child relationship need
  the same state, lift it to their nearest common ancestor and pass it down
  -- prop-drilling two or three levels is still cheaper than the indirection
  a shared store adds, at this app's scale.
- No defensive `useMemo`/`useCallback`/`React.memo` without a measured
  rendering problem -- an app this size re-renders fast enough that
  memoizing ahead of time only adds indirection for no benefit anyone can
  point to.
