#!/bin/sh
# Stand-in `docker` for gate-runner tests (mirrors `fake_docker.sh`'s
# env-var-driven posture, extended to the subcommands `cosmo.gate.docker_runner`
# actually issues: run, network create/rm, port, logs, rm -f).
#
# Every invocation is appended to $FAKE_DOCKER_LOG (one line, args joined by
# a single space) so a test can assert on exactly what was requested.
echo "$*" >>"$FAKE_DOCKER_LOG"

case "$1" in
run)
    if [ -n "$FAKE_DOCKER_STDOUT" ]; then
        printf '%s' "$FAKE_DOCKER_STDOUT"
    fi
    if [ -n "$FAKE_DOCKER_STDERR" ]; then
        printf '%s' "$FAKE_DOCKER_STDERR" >&2
    fi
    exit "${FAKE_DOCKER_EXIT:-0}"
    ;;
port)
    if [ -n "$FAKE_DOCKER_PORT_OUTPUT" ]; then
        printf '%s\n' "$FAKE_DOCKER_PORT_OUTPUT"
        exit 0
    fi
    exit 1
    ;;
logs)
    printf '%s' "$FAKE_DOCKER_LOGS_OUTPUT"
    exit 0
    ;;
network | rm)
    exit 0
    ;;
*)
    exit 0
    ;;
esac
