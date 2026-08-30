# Backend Error Handling

## Error taxonomy

| Category | HTTP status | When |
|---|---|---|
| Validation | 400 | Request fails `@Valid`/Bean Validation, or a business-rule precondition fails |
| Not found | 404 | The requested entity doesn't exist (or the caller can't see it -- see below) |
| Conflict | 409 | A uniqueness or state-transition rule would be violated |
| Unauthorized | 401 | No valid authentication |
| Forbidden | 403 | Authenticated, but not permitted |
| Upstream dependency | 502/503 | A downstream service or the database is unavailable/timed out |
| Unhandled | 500 | Anything not mapped above -- treat every occurrence of this as a bug to triage, not a normal outcome |

A resource a caller isn't authorized to see returns 404, not 403 --
distinguishing the two leaks whether the resource exists to someone who
shouldn't know.

## Response format

Every error response is JSON with a consistent shape:

```json
{
  "status": 404,
  "error": "not_found",
  "message": "Widget 123 not found",
  "path": "/api/widgets/123"
}
```

`error` is a stable machine-readable code (snake_case), `message` is
human-readable and safe to display, `path` is the request path. Never
include a stack trace, an internal exception class name, or a raw SQL error
in the response body.

## Logging vs. surfacing

- Full exception + stack trace: logged server-side always, at `ERROR` for
  5xx and `WARN` for 4xx.
- `message` returned to the client: safe, actionable, and never a leak of
  internal state (no SQL fragments, no stack frames, no internal ids that
  weren't already known to the caller).
- A caught exception that gets rethrown as a different type must not lose
  the original exception as its cause (`throw new X(msg, originalException)`)
  -- losing it is a debugging tax on every future incident.
