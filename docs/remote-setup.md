# Remote FNB58 setup

The power-measurement pipeline can drive a FNB58 that's attached to
another machine over SSH. This is how we wire that up from scratch.

Goal: `fnb58-log` runs on a remote Linux box (typically a Raspberry Pi
with the meter permanently attached), capture artifacts live on the
invoking dev box.

## One-time per-host setup

### 1. Install `fnb58-log` on the remote

```bash
ssh <alias> 'git clone <repo_url> ~/fnb58-logger \
  && cd ~/fnb58-logger \
  && python3 -m venv .venv \
  && .venv/bin/pip install -e ".[markers]"'
```

`markers` extra is only needed if you plan to use `--marker-port`.
Omit it otherwise.

### 2. Grant non-root USB access (Linux only)

```bash
ssh <alias> 'sudo cp ~/fnb58-logger/udev/99-fnirsi.rules /etc/udev/rules.d/ \
  && sudo udevadm control --reload-rules \
  && sudo udevadm trigger'
```

Replug the FNB58 after the rules load. Verify:

```bash
ssh <alias> 'lsusb | grep FNIRSI && ls -l /dev/hidraw*'
```

### 3. SSH alias

In `~/.ssh/config` on your dev box, put:

```sshconfig
Host test-system
  HostName <ip-or-hostname>
  User <remote-user>
  IdentityFile ~/.ssh/id_ed25519
```

Verify with `ssh -o BatchMode=yes test-system "echo OK"`.

### 4. Host config for the pipeline

Copy the shipped template to the user-global config path:

```bash
python scripts/power_host.py init
```

This writes `~/.claude/fnb58-power.json`. Edit it to match your SSH
alias and the remote venv's `fnb58-log` path. The default template
matches the `test-system` rig: repo at `~/fnb58-logger` with a
pre-built `.venv`.

Verify config is picked up:

```bash
python scripts/power_host.py list
python scripts/power_host.py show test-pi
python scripts/power_host.py probe test-pi     # best-effort liveness
```

## Running a measurement over SSH

```bash
# Explicit host
python scripts/power_run.py --scenario scenarios/deep_sleep.json --host test-pi

# Or via slash command
/power-measure deep_sleep --host test-pi

# Skill honours config default_host if you set one:
/power-measure deep_sleep
```

Precedence (first hit wins):

1. `--host <alias>` CLI flag. `--host local` forces local even if a default is set.
2. `$POWER_HOST` environment variable.
3. `scenario.default_host` field.
4. Config's `default_host` field.
5. Nothing → local execution.

## Keeping remote code in sync

Changes to `src/` won't automatically ship to the remote. Two ways to
sync:

**A. Via git (simplest if the remote has a writable checkout):**
```bash
ssh <alias> 'cd ~/fnb58-logger && git pull && .venv/bin/pip install -e .'
```

**B. Via tar-over-ssh (when the remote is a detached copy):**
```bash
tar --exclude=__pycache__ --exclude=*.egg-info --exclude=.pytest_cache \
    --exclude=runs --exclude=.venv --exclude=.git -cf - \
    src tests scripts scenarios pyproject.toml README.md CLAUDE.md \
  | ssh <alias> "cd ~/fnb58-logger && tar -xf -"
ssh <alias> 'cd ~/fnb58-logger && .venv/bin/pip install -e .'
```

`scripts/power_sync.py` wraps option B (shipped separately if/when needed).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ssh: Could not resolve hostname <alias>` | Alias not in `~/.ssh/config`. `power_run` will NOT silently fall back to local. Add the host or pass `--host local`. |
| `scp failed (exit 1)` | Likely the remote `fnb58-log` exited before writing the CSV — see the passed-through stderr. Test with `power_host.py probe <alias>`. |
| `device stopped streaming — try unplugging and replugging` | Known FNB58 wedge. `power_run.py` already sleeps 1 s between repetitions; if it still happens, power-cycle the meter. See CLAUDE.md. |
| Measurements don't include state markers | Check `marker_port` in the scenario refers to the **remote's** serial device (e.g. `/dev/ttyUSB0`), and that `pyserial` is installed in the remote venv. |
