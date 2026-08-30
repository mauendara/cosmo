#!/bin/sh
# Stand-in for the `openspec` CLI (mirrors fake_docker.sh / fake_claude.sh --
# fake the external process, test the mechanics). Implements the two
# invocations Cosmo makes: `init <path> --tools none --force`
# (`ensure_openspec_initialized`) and `archive <name> --yes`
# (`archive_change`, cwd-relative like the real CLI -- see `archive_change`'s
# own docstring). Records every invocation to $FAKE_OPENSPEC_LOG.
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

if [ "$1" = "archive" ]; then
    name="$2"
    mkdir -p "openspec/changes/archive"
    echo "archived" >"openspec/changes/archive/$name.marker"
fi
