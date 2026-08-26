# Deployment (Phase 9, spec 9.5 / spec 1)

`cosmo-run.service` is the systemd unit for running `cosmo run` unattended
-- on the droplet or under WSL2, "identical" per the plan (spec 1). It is
not installed by any Cosmo command; an operator copies/symlinks it in.

## Installing

```bash
sudo cp deploy/cosmo-run.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cosmo-run.service
```

Edit the three per-host values at the top of `[Service]` before enabling:
`WorkingDirectory` (Cosmo's own checkout), `COSMO_CONFIG` (the host's
config file -- droplet paths, not XDG defaults), and the `--repo` path in
`ExecStart` (the target repo Cosmo operates on).

## WSL2

Spec 1 calls for "identical" behavior on the droplet and under WSL2. WSL2
only runs a real systemd (PID 1, not just a compat shim) if
`/etc/wsl.conf` has:

```ini
[boot]
systemd=true
```

**This was checked for real this session, on this host: WSL2 systemd is
enabled here** (`ps -p 1 -o comm=` reports `systemd`; `systemctl --version`
and a real user-session `systemctl --user start` both work). The exit
criterion ("a run under systemd survives a restart, and a deliberately
wedged loop is caught by the watchdog and restarted") was verified for
real on this box with a throwaway `systemctl --user` unit exercising the
same `cosmo.watchdog.notify` code path `run.loop.run_queue` uses --
see `docs/v3-implementation-state.md`'s Phase 9 section for the transcript
summary. A host without `systemd=true` set would need the system unit
installed a different way (e.g. run under a process supervisor other than
systemd, or the standard WSL2 fallback of a login-triggered script) --
not this file's concern, but worth checking on any *new* box before
assuming this unit works there unmodified.

## `Restart=on-failure` + `RestartPreventExitStatus=1`

`cosmo run`'s own exit code is `0` only for a clean `queue_empty`/
`completed` stop; every other stop -- `PAUSED` for the circuit breaker or a
confirmed quota exhaustion (spec 6.5: "resuming requires manual
intervention"), or `STOPPED` for a cost ceiling, a disk-space abort, or a
startup DAG cycle -- exits `1`. None of those are fixed by an immediate
blind restart (a cost or disk ceiling would just be re-hit since a new
`run_id` starts every counter fresh); they need an operator. A genuine
hang, by contrast, never reaches `sys.exit` at all -- systemd's own
`WatchdogSec` kill is a *signal*, not an exit status, so
`RestartPreventExitStatus=1` does not suppress a restart in that case. Both
halves of this were verified for real this session (see the state doc).
