# Installing and using the FNB58 power skills

Five Claude Code slash commands drive the `scripts/power_*.py` wrappers in
this repo. The scripts do all the work (naming, metadata, pass/fail); a
skill only runs its script and reports the result.

| Skill | What it does |
| --- | --- |
| `/power-measure <scenario>` | Records a run; saves CSV + JSON files under `runs/` |
| `/power-baseline [run]` | Pins a run as the reference for the current git branch |
| `/power-validate <scenario> <run>` | Checks a run against the scenario's budgets and the baseline (pass/fail) |
| `/power-compare <A> <B>` | Prints a side-by-side metrics table |
| `/power-host list\|show\|probe\|init` | Configures and checks a remote FNB58 host (SSH) |

## Part 1: Install

### Step 1: Install the logger (once per machine)

```bash
cd C:\Work\fnirsi-usb-power-data-logger
python -m pip install -e ".[markers]"   # "markers" adds pyserial for UART state labels
```

- **Windows:** nothing else is needed. The default hidapi backend works
  with the stock Windows HID driver; Zadig is only needed for the
  `--backend pyusb` fallback (see the README, *Platform notes → Windows 11*).
- **Linux / Raspberry Pi:** install the udev rule so the meter is usable
  without root — see [`remote-setup.md`](remote-setup.md), step 2.
- **Check:** with the FNB58 plugged in,
  `python -m fnb58_logger -d 3 --summary -o test.csv` prints one JSON
  line containing `samples`.

### Step 2: Choose how Claude Code finds the skills

**Option A — work inside this repo (nothing to install).** Start `claude`
in the repo root. Claude Code loads the skills from `.claude/skills/`
automatically, plus a hook that warns when the working tree is dirty.
Type `/power` to list them.

**Option B — install for your user, so the skills work in any repo**
(e.g. your firmware project). Run the installer with the same Python you
used for `pip install -e .` — the installed skills run the scripts with
that interpreter:

```bash
python scripts/install_skills.py              # install / refresh into ~/.claude/skills
python scripts/install_skills.py --dry-run    # preview only
python scripts/install_skills.py --uninstall  # removes only the skills it installed
```

- The installer copies the skills and rewrites their script commands to
  absolute paths (this interpreter + this repo's `scripts/`), so they work
  from any working directory.
- `scenarios/`, `runs/` and the git SHA used in run names still come from
  the repo Claude Code is running in.
- Copies don't follow the source: **re-run the installer after every
  `git pull`** of this repo.
- The dirty-tree hook is project-scoped and is not installed by Option B;
  the `-dirty` suffix on run names still applies.
- If the installer warns that its Python has neither `hid` nor `usb`,
  re-run it with the Python where the logger is installed.

## Part 2: Use it with a firmware project

Goal: measure your firmware, tie every result to a firmware commit, and
catch power regressions. The examples assume Option B and a firmware repo
whose main branch is `master`.

### Step 1: Prepare the firmware repo

1. Add `runs/` to the firmware repo's `.gitignore`. Run names carry a
   `-dirty` suffix whenever `git status` is non-empty — untracked result
   files count, so without this every run after the first is `-dirty`.
2. Create a `scenarios/` folder and commit your scenario files (Step 4).

### Step 2: Wire up the hardware

```text
USB power source ──► FNB58 ──► DUT (your board)
                      │
                      └─ USB data cable ─► PC (or the Pi, for remote use)
DUT UART TX ──► USB-serial adapter ──► PC (e.g. COM5)   ← optional, for state labels
```

### Step 3: Add state markers to the firmware (optional, recommended)

Print one marker line each time the firmware enters a state:

```c
printf("#STATE:BOOT\r\n");
printf("#STATE:DEEP_SLEEP\r\n");   // just before entering sleep
printf("#STATE:RADIO_TX\r\n");
```

- Every CSV row gets the most recently seen label in its `state` column,
  so budgets can be set per state. Other UART output is ignored.
- Labels may use `A-Z a-z 0-9 _ . -`, up to 32 characters, case-sensitive.
- Keep the lines short: the UART staying powered while printing skews the
  surrounding samples.
- Requires the `markers` extra (pyserial) on the machine running
  `fnb58-log`.

### Step 4: Write a scenario for each situation you measure

Save it as `scenarios/<name>.json` in the firmware repo. The file name
must match the `name` field.

```json
{
  "name": "idle_ble",
  "description": "Advertising every 1 s, no connection.",
  "duration_s": 30,
  "min_duration_s": 25,
  "repetitions": 3,
  "expected_states": ["IDLE"],
  "marker_port": "env:MARKER_PORT",
  "marker_baud": 115200,
  "budgets": {
    "overall":   { "max_mean_mA": 0.5, "max_energy_mWh": 0.2 },
    "per_state": { "IDLE": { "max_p95_mA": 1.2 } }
  },
  "regression": {
    "max_mean_mA_regression_pct": 3.0,
    "max_p95_mA_regression_pct": 8.0,
    "max_energy_mWh_regression_pct": 3.0
  },
  "default_host": null
}
```

- `budgets` are hard limits the run must stay under; `regression` limits
  are the largest allowed increase versus the baseline, in percent.
- Loading is strict: an unknown key such as `max_mean_ma` is an error
  instead of being silently ignored.
- Without a UART: set `"expected_states": []`, `"per_state": {}` and
  `"marker_port": null`.
- `"env:MARKER_PORT"` reads the port from an environment variable when
  the run starts: `$env:MARKER_PORT = "COM5"` (PowerShell) or
  `export MARKER_PORT=/dev/ttyUSB0` (bash). If the variable is unset, no
  markers are read and `expected_states` checks fail. You can also pass
  `--marker-port COM5` to `/power-measure`.
- Set regression limits above the natural run-to-run noise of your setup:
  measure the same build a few times and check the `cv` values in the
  `.stability.json`. A 3 % limit on a load that varies by 20 % fails on
  every run.

Full schema: [`../scenarios/README.md`](../scenarios/README.md).

### Step 5: Record a baseline on known-good firmware

```text
git checkout master              # build and flash this commit to the DUT
/power-measure idle_ble
/power-baseline                  # pins the newest run for branch "master"
```

Runs are named `runs/<sha>[-dirty]-<label>-r<N>.csv` (10-character SHA;
the label defaults to the scenario name). Each repetition gets its own
`.csv`, `.summary.json` and `.meta.json`, and a
`runs/<sha>-<label>.stability.json` summarizes all repetitions. With
`"repetitions": 1` there is no `-r<N>` suffix. Measuring the same commit
and label again adds `.1`, `.2`, … instead of overwriting. Commit before
measuring anything you want to keep, or the run is labeled `-dirty`.

`/power-baseline` stores its pointer in `runs/.baseline-<branch>.json`
and prefers the newest `.stability.json` over a single CSV.

### Step 6: Change the firmware, then measure and validate

Baselines are **per branch**. On a new branch, pin the master run once,
otherwise validation fails with "no baseline available":

```text
git checkout -b feature/lower-power
/power-baseline runs/<master-sha>-idle_ble.stability.json

# edit → build → flash → commit
/power-measure idle_ble
/power-validate idle_ble runs/<new-sha>-idle_ble-r1.csv
/power-compare runs/<master-sha>-idle_ble-r1.csv runs/<new-sha>-idle_ble-r1.csv
/power-compare <a.csv> <b.csv> --state IDLE     # one firmware state only
```

- `/power-validate` exits 0 on PASS, 1 on FAIL (every failing check is
  listed verbatim) and 2 on a usage error such as a bad scenario file.
  Instead of pinning, you can pass `--baseline <path>` to it.
- `/power-compare` is a report only and always exits 0. It accepts paths
  or labels; a label resolves to the newest run with that label, so
  comparing by label needs two different labels — record them with
  `/power-measure idle_ble --label before` and `--label after`.

> **Per-state budgets need a single-run CSV.** If the scenario has
> `per_state` budgets, validate a repetition CSV (`-r1.csv`), not the
> `.stability.json`: the stability summary has no per-state data, so the
> per-state check always fails on it. Without `per_state` budgets,
> validating the `.stability.json` is fine and averages out noise.

### Step 7 (optional): Measure through a Raspberry Pi over SSH

If the FNB58 is attached to a Pi instead of your PC, do the one-time
setup in [`remote-setup.md`](remote-setup.md), then:

```text
/power-host init                 # writes ~/.claude/fnb58-power.json; edit ssh_alias and fnb58_log_cmd
/power-host probe test-pi        # checks SSH access and that fnb58-log exists on the Pi
/power-measure idle_ble --host test-pi
```

The CSV is copied back, so all results stay in the local `runs/`. A
scenario can also pin a host with `"default_host"`; `--host local` forces
the local meter.

**Status:** the SSH path is covered only by dry-run tests; it has not yet
been exercised against a real remote FNB58.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Every run is named `…-dirty-…` | `runs/` is not in the firmware repo's `.gitignore`, or there are uncommitted changes. |
| `regression … no baseline available` | No baseline pinned for the current branch — run `/power-baseline <run>` on this branch, or pass `--baseline`. |
| `budget.per_state … stability rollup has no per-state slices` | Validate a `-r1.csv` instead of the `.stability.json`. |
| Missing `expected_states` | `MARKER_PORT` unset, wrong port, pyserial missing, or the firmware doesn't print `#STATE:` lines. |
| `/power-measure` cannot open the FNB58 | The skills' Python lacks `hid`; re-run `install_skills.py` with the Python where the logger is installed. On Windows, see the README (*Platform notes → Windows 11*) for the hidapi "Overlapped I/O" issue. |
| Installed skills behave like an old version | Re-run `python scripts/install_skills.py` after `git pull`. |
