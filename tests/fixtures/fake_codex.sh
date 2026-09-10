#!/bin/sh
# Process stand-in for CodexInvoker tests. No real Codex call belongs in unit tests.
if [ -n "$FAKE_CODEX_LOG" ]; then
    : >"$FAKE_CODEX_LOG"
    for arg in "$@"; do
        printf 'arg:%s\n' "$arg" >>"$FAKE_CODEX_LOG"
    done
    printf 'task:%s\nrole:%s\ndb:%s\n' \
        "$COSMO_TASK_ID" "$COSMO_HARNESS_ROLE" "$COSMO_DB_PATH" >>"$FAKE_CODEX_LOG"
    if [ -n "$CODEX_API_KEY" ]; then
        printf 'CODEX_API_KEY_WAS_SET\n' >>"$FAKE_CODEX_LOG"
    fi
fi

if [ -n "$FAKE_CODEX_STDERR_FILE" ]; then
    while IFS= read -r line; do
        printf '%s\n' "$line" >&2
    done <"$FAKE_CODEX_STDERR_FILE"
fi

if [ -n "$FAKE_CODEX_STREAM_FILE" ]; then
    while IFS= read -r line; do
        printf '%s\n' "$line"
    done <"$FAKE_CODEX_STREAM_FILE"
fi

if [ -n "$FAKE_CODEX_SURVIVAL_MARKER" ]; then
    (
        trap '' TERM
        sleep 5
        printf survived >"$FAKE_CODEX_SURVIVAL_MARKER"
    ) &
    wait
fi

exit "${FAKE_CODEX_EXIT_CODE:-0}"
