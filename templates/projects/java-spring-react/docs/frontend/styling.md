# Frontend Styling

## Approach

Tailwind utility classes directly in JSX for one-off styling; extract a
component (not a `@apply`-based CSS class) once the same cluster of classes
repeats three or more times. Avoid hand-written CSS files except for the
handful of things Tailwind genuinely doesn't cover (e.g. complex keyframe
animations).

## Design tokens

Colors, spacing, and typography scale are defined once in `tailwind.config`
(theme extension), never as inline hex values or magic pixel numbers in a
component -- a component reaching for `#3b82f6` instead of `bg-blue-500` (or
a project-specific token) is a sign the token doesn't exist yet and should
be added, not that this component is a special case.

## Responsive / accessibility baseline

- Every interactive element is reachable and operable via keyboard.
- Every image has meaningful `alt` text (or `alt=""` if genuinely
  decorative).
- Layouts are checked at mobile width before being considered done, not
  only at whatever width the developer's monitor happens to be.
