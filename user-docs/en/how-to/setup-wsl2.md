# How to run Cosmo on Windows via WSL2

Cosmo behaves identically on a Linux VPS and under WSL2. Two things are
specific to WSL2 and both will bite you if you skip them: **filesystem
choice** and **systemd**.

## 1. Keep everything on the WSL2 filesystem

Put Cosmo's checkout, its `work_dir`, and the target repo under `/home/...`
inside the WSL2 distribution — **never** under `/mnt/c/...`.

Files on `/mnt/c` go through the 9p bridge to the Windows filesystem. Maven
and `node_modules` I/O there is slow enough to distort every timeout in
Cosmo's configuration, and it is periodically flaky under Docker. A build
that takes four minutes natively can take twenty on `/mnt/c`, which means
your `implementing_stall` and `stage_timeout_seconds` values are now wrong,
and you'll spend a night watching tasks time out for no reason a log will
explain.

`cosmo doctor` warns about it explicitly:

```
warn  work dir filesystem  /mnt/c/cosmo/work is on a Windows drive mount;
                           builds there are slow enough to distort the
                           timeouts. Prefer a path inside the WSL2 filesystem.
```

It's a warning, not a failure, so it will not stop a run. Treat it as one.

You can still open the repo from Windows — VS Code's WSL remote works fine
against `/home/...`, and `\\wsl$\<distro>\home\you\...` is browsable from
Explorer. Just don't make it the storage location.

## 2. Enable systemd

WSL2 only runs a real systemd (as PID 1, not a compatibility shim) if you
ask for it:

```ini
# /etc/wsl.conf
[boot]
systemd=true
```

Then, from Windows:

```powershell
wsl --shutdown
```

and start the distribution again. Verify:

```bash
ps -p 1 -o comm=      # should print: systemd
systemctl --version
```

Without this, the systemd units in `deploy/` won't work and you'd need
another supervisor — a different process manager, or the usual WSL2 fallback
of a login-triggered script. That's out of scope here, but check it on any
*new* box before assuming the units work unmodified.

## 3. Docker

Either works:

- **Docker Desktop** with WSL2 integration enabled for your distribution.
- **Docker Engine installed inside the distribution** (`sudo apt install
  docker.io`), which is simpler if you don't want Docker Desktop running.

Confirm from inside WSL2, not from PowerShell:

```bash
docker run --rm hello-world
```

`cosmo doctor` checks that `docker` is on `PATH`; the gate needs it to
actually start containers, so run the above too.

## 4. Install and bootstrap

Identical to any Linux host:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# install gitleaks, openspec, and your harness CLI per their own instructions

git clone <this repo> ~/cosmo && cd ~/cosmo
uv sync
uv tool install --editable .

cosmo doctor
cosmo init ~/code/my-app --project-template vite-react-local
cosmo doctor --project-path ~/code/my-app
```

For the full first run, follow the [tutorial](../tutorial.md).

## 5. Run under systemd

The units in `deploy/` are written for system-wide installation, but a user
session is usually what you want on a personal machine:

```bash
mkdir -p ~/.config/systemd/user
cp ~/cosmo/deploy/cosmo-run.service ~/cosmo/deploy/cosmo-notify.service \
   ~/.config/systemd/user/
```

Edit each copy's `[Service]` section for your paths:

```ini
WorkingDirectory=/home/you/cosmo
Environment=COSMO_CONFIG=/home/you/.config/cosmo/config.toml
ExecStart=/home/you/cosmo/.venv/bin/cosmo run --repo /home/you/code/my-app
```

Then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now cosmo-run.service cosmo-notify.service
systemctl --user status cosmo-run.service
journalctl --user -u cosmo-run.service -f
```

Keep the user session alive across logout so an overnight run survives you
closing the terminal:

```bash
sudo loginctl enable-linger $USER
```

The restart semantics are the same as on a VPS and are worth understanding
before you rely on them — see
[setup-vps, step 7](setup-vps.md#7-understand-the-restart-semantics).

One WSL2-specific note on the unit files: `StartLimitIntervalSec` and
`StartLimitBurst` must be under `[Unit]`, not `[Service]`. Recent systemd
rejects them under `[Service]` with "Unknown key … ignoring" in the journal,
and the restart-storm cap silently never applies. The shipped units already
have them in the right place.

## 6. Windows-specific gotchas

**The machine sleeping.** An overnight run ends the moment Windows suspends.
Set the power plan to never sleep while plugged in, or run on a machine that
won't.

**`wsl --shutdown` kills everything.** Including an in-flight run. Cosmo
recovers cleanly on the next start — mid-flight tasks are emitted as
`task.interrupted` and requeued, and the abandoned `run_state` row is closed
out as `crashed` — but the work in progress is lost.

**Memory.** WSL2 takes a fraction of host RAM by default. A Maven build plus
Playwright plus Chromium is not a small workload. If containers are being
OOM-killed, raise the limit:

```ini
# %UserProfile%\.wslconfig  (on Windows, then `wsl --shutdown`)
[wsl2]
memory=12GB
```

**Line endings.** If Windows Git checked the repo out with CRLF, shell
scripts and hooks inside it will misbehave under WSL2. Clone from inside
WSL2, or set `core.autocrlf=input` in the target repo.

**Disk.** The WSL2 virtual disk grows but does not shrink on its own. Docker
images, worktrees and logs accumulate. `disk.min_free_gb` aborts a run before
it starts rather than letting a full disk fail every task with errors that
read like code errors — but you still have to prune Docker images yourself.

**Clock skew after resume.** A suspended VM can wake with a skewed clock,
which shows up as nonsense event timestamps and durations. If you see it,
`sudo hwclock -s` from inside WSL2.
