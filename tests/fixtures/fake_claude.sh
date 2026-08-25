#!/bin/sh
# Stand-in for the `claude` CLI so ClaudeCodeAdapter unit tests never invoke
# the real binary (mirrors fake_docker.sh's stance, now applied to `claude -p`
# per the Phase 3 handoff -- "build FakeHarnessAdapter as the thing every
# later phase's tests target, and keep the real CLI invocation to the one
# integration exit criterion").
#
# Records the full invocation to $FAKE_CLAUDE_LOG, one line per call. If
# ANTHROPIC_API_KEY is set in this process's environment, also records that
# fact -- proves the adapter's env-scrubbing actually reached the child,
# rather than just trusting the Python-side dict before Popen.
#
# If $FAKE_CLAUDE_STREAM_FILE is set, its lines are streamed to stdout
# (one $FAKE_CLAUDE_DELAY-second sleep between each, if set) before exiting
# with ${FAKE_CLAUDE_EXIT_CODE:-0}.
echo "$@" >>"$FAKE_CLAUDE_LOG"

if [ -n "$ANTHROPIC_API_KEY" ]; then
    echo "ANTHROPIC_API_KEY_WAS_SET" >>"$FAKE_CLAUDE_LOG"
fi

if [ -n "$FAKE_CLAUDE_STREAM_FILE" ]; then
    while IFS= read -r line; do
        echo "$line"
        if [ -n "$FAKE_CLAUDE_DELAY" ]; then
            sleep "$FAKE_CLAUDE_DELAY"
        fi
    done <"$FAKE_CLAUDE_STREAM_FILE"
fi

exit "${FAKE_CLAUDE_EXIT_CODE:-0}"
