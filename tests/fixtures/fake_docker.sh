#!/bin/sh
# Stand-in for the `docker` CLI so orphan-sweep tests never need a live
# daemon (mirrors the plan's stance on `claude -p`: fake the external
# process, test the mechanics). Records every invocation to
# $FAKE_DOCKER_LOG; for `ps -q ...` prints the ids in
# $FAKE_DOCKER_CONTAINERS, one per line.
#
# If $FAKE_DOCKER_FAIL is set, prints it to stdout and exits 1 instead --
# reproducing the real WSL2 Docker Desktop shim, which prints its
# "could not be found" banner to stdout rather than stderr on failure.
if [ -n "$FAKE_DOCKER_FAIL" ]; then
    echo "$@" >>"$FAKE_DOCKER_LOG"
    echo "$FAKE_DOCKER_FAIL"
    exit 1
fi
echo "$@" >>"$FAKE_DOCKER_LOG"
case "$1" in
ps)
    for id in $FAKE_DOCKER_CONTAINERS; do
        echo "$id"
    done
    ;;
esac
