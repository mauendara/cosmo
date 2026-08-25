#!/bin/sh
# Stand-in for the `openspec` CLI (mirrors fake_docker.sh / fake_claude.sh --
# fake the external process, test the mechanics). Only implements the one
# invocation `ensure_openspec_initialized` makes: `init <path> --tools none
# --force`. Records every invocation to $FAKE_OPENSPEC_LOG.
#
# If $FAKE_OPENSPEC_FAIL is set, prints it to stderr and exits 1 instead.
echo "$@" >>"$FAKE_OPENSPEC_LOG"

if [ -n "$FAKE_OPENSPEC_FAIL" ]; then
    echo "$FAKE_OPENSPEC_FAIL" >&2
    exit 1
fi

if [ "$1" = "init" ]; then
    target="$2"
    mkdir -p "$target/openspec/changes/archive" "$target/openspec/specs"
    echo "schema: spec-driven" >"$target/openspec/config.yaml"
    echo "OpenSpec structure created"
fi
