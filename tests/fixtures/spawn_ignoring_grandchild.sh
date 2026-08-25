#!/bin/sh
# Test fixture for Phase 2's process-supervision exit criterion: a process
# tree where SIGTERM alone cannot bring everything down.
#
# This script (the direct child of ManagedProcess) has no SIGTERM trap, so it
# dies immediately when signaled -- exactly the case that fools a supervisor
# which declares victory once its own direct child exits. Its grandchild
# ignores SIGTERM outright and loops forever, so full reaping requires (a)
# killpg reaching a process that isn't the direct child, and (b) escalating
# to SIGKILL when SIGTERM doesn't clear the group.
sh -c 'trap "" TERM; while true; do sleep 0.1; done' &
echo "$!"
wait
