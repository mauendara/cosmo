# Frontend Styling

## Approach

Tailwind utility classes directly in JSX for one-off styling; extract a
component (not a `@apply`-based CSS class) once the same cluster of classes
repeats three or more times. No CSS-in-JS, and no hand-written CSS files
except for the handful of things Tailwind genuinely doesn't cover (e.g. a
custom keyframe animation for a card-flip transition).

## Mobile-first

Write the unprefixed (mobile) styles first, then layer `sm:`/`md:`/`lg:`
variants on top for larger viewports -- not the reverse. A layout is not
done until it's been checked at mobile width, not only at whatever width the
developer's monitor happens to be; this matters more than usual for the
grid-based games (Memory, Snake, 2048), where a grid that fits comfortably
on a desktop viewport can overflow or force horizontal scrolling on a phone.

## Design tokens

Colors, spacing, and typography scale are defined once in `tailwind.config`
(theme extension), never as inline hex values or magic pixel numbers in a
component -- a component reaching for `#3b82f6` instead of `bg-blue-500` (or
a project-specific token) is a sign the token doesn't exist yet and should
be added, not that this component is a special case.

## Accessibility baseline

- Every interactive element (including a game board's cells) is reachable
  and operable via keyboard, not only pointer/touch -- this is also what
  makes `getByRole` semantic e2e queries possible in the first place (see
  `testing.md`); a `<div onClick>` with no role or keyboard handler is
  invisible to both.
- Every image has meaningful `alt` text (or `alt=""` if genuinely
  decorative).
