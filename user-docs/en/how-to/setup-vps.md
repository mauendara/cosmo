# How to run Cosmo unattended on a VPS

Goal: `cosmo run` drains the queue overnight under systemd, restarts itself
if it wedges, does *not* restart itself when a human needs to intervene, and
tells you what happened.

Assumes a Debian/Ubuntu-family host with systemd and root access.

## 1. Install the dependencies

```bash
sudo apt update
sudo apt install -y git docker.io curl
sudo systemctl enable --now docker
```

Then, as the user Cosmo will run as:

```bash
# uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# gitleaks and openspec: install per their own project instructions
# your harness CLI (Claude Code today): per its own instructions
```

Add the run user to the `docker` group so the gate can start containers
without sudo:

```bash
sudo usermod -aG docker cosmo
```

## 2. Install Cosmo

The unit file assumes Cosmo's own checkout at `/opt/cosmo`:

```bash
sudo git clone <this repo> /opt/cosmo
sudo chown -R cosmo:cosmo /opt/cosmo
cd /opt/cosmo
uv sync
```

That creates `/opt/cosmo/.venv/bin/cosmo`, which is what the unit invokes.
Keep the checkout — Cosmo reads its project and harness templates from
`templates/` in the repository, not from an installed wheel.

## 3. Choose your paths

XDG defaults put state under the run user's home. On a server, put it
somewhere explicit and sized for it:

```bash
sudo mkdir -p /var/cosmo/{work,logs} /etc/cosmo
sudo chown -R cosmo:cosmo /var/cosmo /etc/cosmo
```

```toml
# /etc/cosmo/config.toml
[paths]
data_dir = "/var/cosmo"
work_dir = "/var/cosmo/work"
log_dir  = "/var/cosmo/logs"

[git]
base_branch = "develop"

[disk]
min_free_gb = 20.0
```

```bash
sudo chmod 600 /etc/cosmo/config.toml
sudo chown cosmo:cosmo /etc/cosmo/config.toml
```

`600` matters — this file will hold your notification bot token. Keep it out
of any repository.

Size the volume for what actually accumulates: one worktree per in-flight
task, Docker images (the Playwright image alone is multi-gigabyte), and
harness logs. `disk.min_free_gb` aborts a run before it starts rather than
letting a full disk fail every task with errors that read like code errors.

## 4. Set up the target repo

Cosmo needs its **own checkout** of the repo it works on. Never point it at
a directory a human also works in — it keeps that checkout on the base
branch at all times so the merge ladder can run against it directly.

```bash
sudo -u cosmo git clone <your project> /var/cosmo/target-repo
sudo -u cosmo /opt/cosmo/.venv/bin/cosmo init /var/cosmo/target-repo \
    --project-template java-spring-react \
    --git-author-name "Cosmo" --git-author-email cosmo@yourdomain
```

Set `COSMO_CONFIG=/etc/cosmo/config.toml` in your shell, or pass
`--config /etc/cosmo/config.toml`, for every manual invocation.

## 5. Verify before automating

```bash
sudo -u cosmo COSMO_CONFIG=/etc/cosmo/config.toml \
    /opt/cosmo/.venv/bin/cosmo doctor --project-path /var/cosmo/target-repo
```

Fix every `FAIL`. Common ones on a fresh box:

- `docker` — the run user isn't in the `docker` group, or the group change
  hasn't taken effect in this session.
- `subscription billing` — `ANTHROPIC_API_KEY` is exported somewhere in the
  user's profile. Unset it; it silently switches to per-token billing.
- `disk space` — below `disk.min_free_gb`.

Then smoke-test the harness itself, since a working CLI on your laptop
doesn't imply a working one here:

```bash
sudo -u cosmo COSMO_CONFIG=/etc/cosmo/config.toml \
    /opt/cosmo/.venv/bin/cosmo harness probe --prompt "reply with the word ok"
```

## 6. Install the systemd units

Two units ship in `deploy/`. Neither is installed by any Cosmo command — you
copy them in.

```bash
sudo cp /opt/cosmo/deploy/cosmo-run.service \
        /opt/cosmo/deploy/cosmo-notify.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
```

Before enabling, edit the three per-host values in `cosmo-run.service`'s
`[Service]` section — the only settings a unit file can't get from Cosmo's
config:

| Directive | Set it to |
| --- | --- |
| `WorkingDirectory` | Cosmo's own checkout (`/opt/cosmo`) |
| `Environment=COSMO_CONFIG` | your config file (`/etc/cosmo/config.toml`) |
| the `--repo` path in `ExecStart` | the **target** repo (`/var/cosmo/target-repo`) |

Add a `User=` line if you aren't running as root. `cosmo-notify.service`
needs the same `WorkingDirectory` and `COSMO_CONFIG` and nothing else.

```bash
sudo systemctl enable --now cosmo-run.service cosmo-notify.service
sudo systemctl status cosmo-run.service
sudo journalctl -u cosmo-run.service -f
```

## 7. Understand the restart semantics

This is the part worth getting right, because the naive configuration turns
"a human needs to look at this" into an infinite loop.

```ini
Restart=on-failure
RestartPreventExitStatus=1
RestartSec=30
```

`cosmo run` exits `0` only for a clean `completed` or `queue_empty` stop.
Every deliberate stop — a circuit-breaker pause, a confirmed quota
exhaustion, a cost ceiling, a disk abort, a startup DAG cycle — exits `1`.
None of those are fixed by restarting: a new run starts every counter fresh,
so a cost or disk ceiling would simply be re-hit. `RestartPreventExitStatus=1`
makes systemd leave them alone.

A genuinely wedged process is a different case. It never reaches `sys.exit`
at all — `WatchdogSec` fires and systemd kills it with a *signal*, which is
not an exit status, so the exclusion doesn't apply and it does restart.

```ini
Type=notify
WatchdogSec=10800
```

The loop pings the watchdog at every run-level state transition and once per
task the scheduler picks up. That's coarse on purpose: a single healthy task
can legitimately run for over two hours at default timeouts with no ping in
between, so `WatchdogSec` is set well above that worst case. The consequence
is that a wedged single task is caught at the next task-boundary ping after
the timeout, not immediately. **Retune `WatchdogSec` if you retune
`timeouts.*`** — the two are coupled.

```ini
StartLimitIntervalSec=3600
StartLimitBurst=5
```

These live in `[Unit]`, not `[Service]`. systemd rejects them under
`[Service]` (you'll see "Unknown key … ignoring" in the journal) and the
restart-storm cap silently never applies.

```ini
OOMPolicy=stop
MemoryAccounting=yes
# MemoryMax=4G
```

Never silently respawn into the same memory pressure that just killed it. A
ten-hour run driving Docker, buffering stream JSON and running Playwright is
exactly the workload to watch for a slow leak. `MemoryMax` is left commented
out rather than guessed — size it once you have real usage numbers.

## 8. Set up notifications

You will not be watching the journal at 3am.

```bash
sudo -u cosmo COSMO_CONFIG=/etc/cosmo/config.toml \
    /opt/cosmo/.venv/bin/cosmo notify config
```

The wizard prompts for a bot token, discovers the chat id via `getUpdates`
(walking you through messaging the bot first — bots can't message first),
writes `[notify]` into your config file, and sends one real test message
before declaring success. Then:

```bash
sudo systemctl restart cosmo-notify.service
```

`cosmo-notify.service` is a separate unit with **no ordering dependency** on
`cosmo-run.service`. That's deliberate: its whole value is watching for the
*absence* of activity, including the case where the run unit never started or
died before writing anything. Delivery inside the run loop could never report
the run loop's own crash.

Consider `min_severity = "info"` for a first deployment — the default
`warning` is quiet enough that you might not hear from a healthy run at all.

## 9. Operating it

```bash
# how did last night go
sudo -u cosmo COSMO_CONFIG=/etc/cosmo/config.toml cosmo report

# what's still stuck
cosmo queue ls --status blocked
cosmo queue failures <task_id>

# resume after a circuit-breaker pause, once you've fixed the cause
cosmo run resume
```

A `PAUSED` run needs a human. `cosmo run resume` re-attaches to it with cost
accounting, the reconciliation sweep and the process lock all applying
exactly as for a fresh run.

Queue tomorrow's work whenever you like — `cosmo spec add` /
`cosmo spec queue` are safe to run while a run is in flight; the scheduler
recomputes the eligible set on every pass.

## Hardening notes

- The host holds real credentials, so treat it as one. Cosmo never uses
  `bypassPermissions`; keep it that way.
- Cosmo only ever merges to `git.base_branch`. Merging to `main`/`master`
  stays a human step, and nothing in an unattended run should have push
  access to it.
- Keep `/etc/cosmo/config.toml` at mode `600`. It holds your bot token.
- See [SECURITY.md](../../../SECURITY.md) for the full threat model.
