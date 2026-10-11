# Power-measurement Skill pipeline — plan

Status: v2 — approved 2026-04-23 and implemented (Phases 1–5 done;
`power_optimize.py`, `power_sync.py` and `/power-plot` deferred). Smoke-
tested on real hardware (FNB58, Windows, local mode) 2026-10-11.

Author: Claude (plan mode), 2026-04-23.

v3 changelog (2026-10-11):
- §4/§15: `install_skills.py` drops symlink mode. Installed SKILL.md
  copies get `python scripts/power_*.py` rewritten to the installer's
  interpreter + the logger repo's absolute `scripts/` path; the
  relative paths only resolved when Claude Code ran inside this repo,
  so user-scoped skills could not find the scripts from a firmware
  repo. `scenarios/`, `runs/` and the git SHA stay cwd-relative.

v2 changelog (2026-04-23):
- §2/§3/§4: added `scripts/install_skills.py`, `scripts/power_host.py`,
  `scripts/power_sync.py`, `/power-host` skill, `--host` flag on run/optimize.
- §5: scenarios may carry a `default_host` hint.
- §8: new tests for host-config loader and install-skills idempotency.
- §11: three new risk rows (SSH absent, remote repo drift, summary on stderr).
- §12: acceptance criteria for multi-machine + remote SSH.
- §15 new: Multi-machine skill installation.
- §16 new: Remote FNB58 access over SSH.

## 1. Goal and non-goals

### Goal

Turn the existing `claude-commands/{measure,compare,baseline}.md` prompt
templates — which rely on the model to assemble bash commands, read files,
and judge pass/fail — into a deterministic, test-covered pipeline anchored
by:

- plain CLI **wrapper scripts** under `scripts/power_*.py` that do the
  measurement plumbing (git sha, naming, subprocess spawn, summary JSON,
  metadata sidecars, validation against a scenario file, regression vs
  baseline),
- thin **Skills** under `.claude/skills/power-*/SKILL.md` that just
  invoke those wrappers, surface the JSON, and trust the exit code,
- **scenarios** as versionable JSON specs declaring the budget / regression
  limits / required states for one measurable situation,
- an **append-only `runs/index.jsonl`** so any tool can enumerate past
  runs by scenario/label/sha/branch without walking the filesystem.

The whole thing must be usable without an FNB58 attached, via a
`--replay` seam that feeds a pre-recorded CSV through the real summary
code path — so we can add CI-grade tests (`tests_e2e/`).

### Non-goals (explicitly out of scope)

- No MCP server in this round. A one-paragraph stub of future tool shapes
  is in §11; actual implementation is Phase 2 of the broader initiative,
  tracked separately.
- No real build or flash actions in the demo test project. `power_optimize`
  treats `build_cmd`/`flash_cmd` as opaque shell strings and happily runs
  `echo` stubs — we exercise the loop, not the hardware.
- No GitHub Actions job. Local `pytest` and `bash tests_e2e/run.sh` must
  be green; CI wiring is deferred.
- No matplotlib plot command in this round. Noted as a possible stretch
  only if all acceptance criteria pass with time left — see §11.
- **No auto-sync of source code to the remote host.** `scripts/power_sync.py`
  is a convenience wrapper around the existing tar-over-ssh pattern
  (§16) — it does not try to install Python deps, manage venvs, or set
  up udev rules on the remote. Remote prerequisites (working `fnb58-log`
  in a venv, udev rule installed) remain a one-time manual setup.
- No credential handling. SSH auth is delegated to `~/.ssh/config` and
  the user's key agent — we only invoke `ssh <alias>` and inherit what
  the OS provides.
- No rewrite of `scripts/compare.py`. We import from it, never modify it.
- No changes to `src/fnb58_logger/protocol.py`. Every invariant in
  CLAUDE.md §"Protocol invariants to preserve" is frozen: 64-byte frames,
  CRC-8 params, `"<IIHHBH"` sample unpack, CSV_COLUMNS order, the `state`
  column, etc.

## 2. File tree

```
docs/plans/power-skill.plan.md          (this file)
scripts/_power_common.py                shared helpers: git, scenarios, index, atomic write, host config
scripts/power_run.py                    wraps fnb58-log; writes CSV/summary/meta; stability rollup; --host <alias> for remote
scripts/power_validate.py               the gatekeeper: budget + required-states + regression
scripts/power_baseline.py               write runs/.baseline-<branch>.json
scripts/power_compare.py                thin wrapper over scripts/compare.py with label resolution
scripts/power_optimize.py               optional loop driver (build -> flash -> run -> validate)
scripts/power_host.py                   list/validate host config; dry-run an ssh probe
scripts/install_skills.py               copy or symlink .claude/skills/power-* into ~/.claude/skills/
scripts/power_sync.py                   (optional) tar-over-ssh code-sync convenience wrapper
scenarios/README.md                     scenario schema docs
scenarios/deep_sleep.json               example scenario: DEEP_SLEEP budget
scenarios/radio_tx.json                 example scenario: RADIO_TX budget
.claude/skills/power-measure/SKILL.md   skill frontmatter + body
.claude/skills/power-validate/SKILL.md
.claude/skills/power-baseline/SKILL.md
.claude/skills/power-compare/SKILL.md
.claude/skills/power-optimize/SKILL.md  (only if power_optimize.py ships)
.claude/skills/power-host/SKILL.md      inspect/validate host config; invoke power_host.py
.claude/README.md                       how the Skills are installed (incl. multi-machine workflow)
.claude/settings.json                   one UserPromptSubmit hook (dirty-tree warning)
docs/remote-setup.md                    one-time per-host setup guide (SSH alias, venv, udev)
docs/examples/fnb58-power.example.json  host-config template (shipped; copied to ~/.claude/ on install)
claude-commands/README.md               MODIFIED: deprecation + pointer to .claude/skills/
claude-commands/measure.md              MODIFIED: prepend DEPRECATED banner
claude-commands/compare.md              MODIFIED: prepend DEPRECATED banner
claude-commands/baseline.md             MODIFIED: prepend DEPRECATED banner
runs/.gitkeep                           ensure the dir exists in git
.gitignore                              MODIFIED: whitelist runs/.gitkeep, whitelist tests_e2e/fixtures/*.csv
tests_e2e/fixtures/gen_fixtures.py      deterministic CSV generator (commit alongside CSVs)
tests_e2e/fixtures/good_deep_sleep.csv  passes deep_sleep.json
tests_e2e/fixtures/regressed_deep_sleep.csv  fails regression vs good
tests_e2e/fixtures/missing_state.csv    never enters DEEP_SLEEP — fails required-states check
tests_e2e/test_power_skill.py           pytest suite (runs all wrappers end-to-end via --replay)
tests_e2e/run.sh                        shell harness: pytest + final banner
tests_e2e/__init__.py                   empty (so pytest-collects)
```

No file under `src/` is modified. If a helper needs to be exposed, it
will be re-exported from the package `__init__` and the plan updated.

## 3. Skill surface

Each skill has a `/power-<name>` slash command. Skills never compute
pass/fail themselves — they invoke the wrapper, print the JSON, and
surface the exit code.

| Skill            | argument-hint                                       | calls                                  |
|------------------|-----------------------------------------------------|----------------------------------------|
| `power-measure`  | `<scenario> [--label L] [--host A] [--replay PATH]` | `scripts/power_run.py`                 |
| `power-validate` | `<scenario> <candidate> [--baseline B]`             | `scripts/power_validate.py`            |
| `power-baseline` | `[<run_csv_or_stability_json>]`                     | `scripts/power_baseline.py`            |
| `power-compare`  | `<baseline> <candidate> [--state NAME]`             | `scripts/power_compare.py`             |
| `power-optimize` | `<scenario> [--host A] [--max-attempts N]`          | `scripts/power_optimize.py` (optional) |
| `power-host`     | `[list\|show <alias>\|probe <alias>]`               | `scripts/power_host.py`                |

Body rules (each SKILL.md includes these verbatim):

- "Do not compute pass/fail yourself. Trust the wrapper's exit code."
- "Never edit scripts/, src/, or scenarios/ from within this skill —
  only read and invoke."
- "Surface the exact JSON result and the run file paths so the user can
  open them."

## 4. Script contracts

Shared:

- All scripts use **argparse** with `--help`.
- `--json` on human-default scripts switches stdout to machine JSON.
  Wrappers whose primary consumer is another script default to JSON.
- **stdout = machine output; stderr = human status**. Never mix.
- Exit codes:
  - `0` — success.
  - `1` — policy/validation failure (e.g. budget exceeded, missing
    state, regression breach).
  - `2` — usage error (invalid args, scenario parse error).
  - `3` — hardware / transport error (USB open failed, subprocess of
    `fnb58-log` exited non-zero with no summary JSON).

### scripts/_power_common.py

Utilities used by every wrapper. No public API beyond these functions;
no CLI. Stdlib only.

- `git_sha(short: int = 10) -> str` — returns `"<hex>"`, `"<hex>-dirty"`,
  or `"nogit"`.
- `git_branch() -> str` — `"<name>"` or `"detached"` / `"nogit"`.
- `load_scenario(path: Path) -> dict` — reads, validates against §5
  schema (type checks, required fields, budget/regression sub-shapes);
  raises `SystemExit(2)` with a one-line `error: ...` on failure.
- `resolve_run_paths(out_dir, sha, label, repetition_index | None) ->
  (csv, summary_json, meta_json)` — applies `.N` uniqueness.
- `atomic_write_json(path, obj) -> None` — `os.replace` via a sibling
  `.tmp` file.
- `append_index_line(index_path, obj) -> None` — opens with `"a"`, writes
  `json.dumps(obj, separators=(",",":")) + "\n"`, flushes.
- `load_candidate_metrics(path: Path) -> dict` — accepts either a CSV
  (delegates to `scripts.compare.summarize(compare._load(...))`) or a
  stability.json rollup (returns `stats` subtree's mean values as the
  candidate metric dict).
- `resolve_latest(out_dir, scenario | label) -> Path | None` — newest
  mtime matching `runs/<sha>-<label>*.csv` (or `-r1.csv` if it exists).
- `load_host_config(explicit_path: Path | None = None) -> dict` — §16
  lookup order: `$FNB58_POWER_HOST_CONFIG` → project
  `.claude/fnb58-power.local.json` → user `~/.claude/fnb58-power.json`.
  Returns `{"hosts": {...}, "default_host": <name|null>}`; empty dict
  if nothing found (not an error — local-only mode is supported).
- `resolve_host(cfg, cli_arg: str | None, scenario_default: str | None)
  -> dict | None` — precedence: `--host` CLI > `$POWER_HOST` env >
  `scenario.default_host` > `cfg.default_host`. Returns the host entry
  or `None` for local execution.

### scripts/power_run.py

Wraps `fnb58-log`.

```
usage: power_run.py --scenario PATH [--label L] [--host ALIAS] [--replay PATH]
                    [--marker-port P] [--marker-baud B]
                    [--out-dir runs] [--runs-dir runs]   # alias; either accepted
                    [--host-config PATH] [--json]
```

- `--replay PATH` (hidden test hook): instead of spawning `fnb58-log`,
  read the given CSV, synthesize a valid summary dict using
  `SummaryAccumulator` (by replaying each row through `.update(row)`),
  and emit both the CSV (copied) and `.summary.json` under `runs/`.
  `--replay` short-circuits host resolution — replay is always local.
- `--host ALIAS` (optional): execute on a remote machine resolved via
  §16 host config. When set, the subprocess becomes
  `ssh <ssh_alias> '<fnb58_log_cmd> -o <remote_tmp_csv> -d <dur> --summary'`,
  the summary JSON is captured from the child's stdout, and the remote
  CSV is pulled back with `scp`/`ssh cat`. All other behavior (naming,
  meta, index) is identical. `--host` can be `local` to force local
  execution even when `cfg.default_host` is set.
- `--host-config PATH`: override the config-file lookup order (for
  tests and exotic setups).
- `--out-dir` defaults to `runs`. `--runs-dir` is accepted as an alias
  so tests can point at `tmp_path` without a second flag system.

Output path scheme:

```
runs/<sha>-<label>[.N][-r<rep>].csv
runs/<sha>-<label>[.N][-r<rep>].summary.json
runs/<sha>-<label>[.N][-r<rep>].meta.json
runs/<sha>-<label>[.N].stability.json   (only if repetitions > 1)
```

`.N` uniqueness is applied at the `-<label>` level *before* `-r<rep>` is
appended. So three repetitions into a fresh dir land at
`<sha>-foo-r1.{csv,...}`, `<sha>-foo-r2.{csv,...}`, `<sha>-foo-r3.{...}`,
plus `<sha>-foo.stability.json`. A second call with the same label bumps
to `<sha>-foo.1-r1...` etc.

Meta sidecar schema:

```json
{
  "label": "deep_sleep",
  "scenario": "deep_sleep",
  "sha": "abcdef0123",
  "dirty": false,
  "branch": "main",
  "hostname": "dev-box",
  "target_board": "nrf52840dk",         // or null
  "timestamp_utc": "2026-04-23T12:34:56Z",
  "fnb58_log_args": ["-d","30","--summary"],
  "repetition_index": 1,                 // 1-based
  "total_repetitions": 3,
  "replay": "tests_e2e/fixtures/good_deep_sleep.csv",  // or null
  "host": {                              // or null for local
    "alias": "test-pi",
    "ssh_alias": "test-pi",
    "remote_repo": "~/fnb58-logger",
    "remote_csv": "/tmp/fnb58-<run-id>.csv"
  }
}
```

Stability rollup JSON:

```json
{
  "label": "deep_sleep",
  "scenario": "deep_sleep",
  "sha": "abcdef0123",
  "runs": ["runs/<sha>-deep_sleep-r1.summary.json", "..."],
  "repetitions": 3,
  "stats": {
    "mean_mA":    {"mean": 0.087, "stdev": 0.003, "cv": 0.034, "min": 0.083, "max": 0.091},
    "p95_mA":     {"mean": 0.076, "stdev": 0.002, "cv": 0.026, "min": 0.074, "max": 0.079},
    "energy_mWh": {"mean": 0.1305, "stdev": 0.005, "cv": 0.038, "min": 0.125, "max": 0.136}
  }
}
```

Between repetitions the script sleeps `1.0 s` — the FNB58-wedge
mitigation. Each captured run appends one line to `runs/index.jsonl`.

On real-hardware mode, the subprocess argv is:

```
fnb58-log -o <csv> -d <scenario.duration_s> --summary
          [--marker-port ...] [--marker-baud ...]
```

The child's stdout (summary JSON) is captured into `<summary_json>`;
the child's stderr is tee'd to our stderr. Non-zero child exit → exit 3.

Final stdout: either the single run's summary JSON, or the stability
rollup JSON, as a single line.

### scripts/power_validate.py

The gatekeeper.

```
usage: power_validate.py --scenario PATH --candidate PATH
                          [--baseline PATH] [--json] [--runs-dir DIR]
```

- `--candidate` accepts a run CSV or a stability.json. For CSV, metrics
  come from `compare.summarize(compare._load(path, state_filter=None))`
  and per-state metrics come from `compare.summarize(compare._load(path,
  state_filter=<state>))`. For stability.json, metrics come from
  `stats.<metric>.mean` (aggregate) and there are no per-state slices —
  validate with a clear "stability rollup has no per-state; use a single
  CSV for per-state budgets" error if `per_state` budgets exist.
- `--baseline`: same acceptance. If absent, fall back to
  `runs/.baseline-<branch>.json`; if that too is absent and the scenario
  demands a regression check, fail with `"no baseline available: run
  power-baseline first"`.

Checks, in order (all run; any failure → exit 1):

1. **duration**: `candidate.duration_s >= scenario.min_duration_s`.
2. **required_states**: every name in `scenario.expected_states` appears
   in the CSV's `state` column at least once. For stability.json
   candidates, read this from the accompanying `.summary.json` files
   (unioned `states` arrays).
3. **budgets.overall**: each key `max_<metric>` in `budgets.overall`
   compared against the aggregate metric.
4. **budgets.per_state**: for each state S in `budgets.per_state`, load
   the candidate CSV with `state_filter=S` and compare each key.
5. **regression**: for each key `max_<metric>_regression_pct` in
   `scenario.regression`, compute
   `(candidate - baseline) / baseline * 100`. Passes if ≤ limit OR if
   `baseline <= 0` (can't regress from zero; pass with note).

JSON shape exactly as specified in the task prompt:

```json
{
  "scenario": "deep_sleep",
  "candidate": "runs/<sha>-deep_sleep.csv",
  "baseline": "runs/<sha-of-baseline>-deep_sleep.stability.json",
  "checks": [
    {"name": "duration", "pass": true, "detail": "30.0s >= 25s"},
    {"name": "required_states", "pass": true, "missing": []},
    {"name": "budget.overall.max_mean_mA", "pass": true, "value": 0.087, "max": 0.10},
    {"name": "budget.DEEP_SLEEP.max_p95_mA", "pass": false, "value": 0.09, "max": 0.08},
    {"name": "regression.mean_mA", "pass": true, "delta_pct": 1.2, "limit_pct": 3.0}
  ],
  "pass": false,
  "failures": ["budget.DEEP_SLEEP.max_p95_mA: 0.09 > 0.08"]
}
```

Human mode (no `--json`): print check names and pass/fail bullets, then
a final `PASS` or `FAIL` line. Exit code: `0` iff `pass: true`.

### scripts/power_baseline.py

```
usage: power_baseline.py [<run_or_stability_json>] [--branch B] [--runs-dir DIR] [--json]
```

- If no arg: pick the newest `runs/*.stability.json` if any, else newest
  `runs/*-*.csv` (excluding `-r*` repetition parts).
- If the arg has a sibling `.meta.json`, inline `sha`, `dirty`, `label`
  into `.baseline-<branch>.json`.
- Writes `runs/.baseline-<branch>.json` atomically. If one existed, print
  its contents to stderr first (so the user sees what they overwrote).
- Stdout: the new baseline JSON blob.

Baseline pointer schema:

```json
{
  "branch": "main",
  "csv": "runs/<sha>-deep_sleep.stability.json",  // or .csv
  "sha": "abcdef0123",
  "dirty": false,
  "label": "deep_sleep",
  "set_at_utc": "2026-04-23T12:40:00Z"
}
```

### scripts/power_compare.py

```
usage: power_compare.py <baseline> <candidate> [--state NAME] [--runs-dir DIR] [--json]
```

- If `<baseline>` or `<candidate>` is not a path that exists, treat it as
  a label and resolve via `_power_common.resolve_latest`.
- If both args resolve, invoke `scripts.compare.main([...resolved..., --json if asked])`
  as a library call (not subprocess) so we reuse exit behavior and table
  formatting unmodified.
- This wrapper never edits `scripts/compare.py`.

### scripts/power_optimize.py (optional; ship if §12 still has time)

```
usage: power_optimize.py --scenario PATH [--max-attempts N] [--runs-dir DIR]
```

Loop: `build_cmd && flash_cmd && power_run && power_validate`, until
validate exits 0 or attempts exhausted. Each iteration creates a new
labeled run. `build_cmd` / `flash_cmd` default to `echo "…"` if the
scenario leaves them null (keeps the script testable without hardware).
Accepts `--host` and forwards it to `power_run`.

### scripts/power_host.py

```
usage: power_host.py list
       power_host.py show <alias>
       power_host.py probe <alias>          # optional; skipped in tests
       power_host.py init [--path PATH]     # write a template config
```

- `list`: prints each host's `alias`, `ssh_alias`, `remote_repo`,
  whether it is the default, and whether the SSH alias appears in
  `~/.ssh/config`. JSON with `--json`.
- `show <alias>`: dumps the resolved entry, including any env
  overrides that would apply.
- `probe <alias>`: best-effort liveness check — runs
  `ssh <ssh_alias> -o BatchMode=yes -o ConnectTimeout=5 "test -x <fnb58_log_cmd> && echo OK"`.
  Exit 0 on OK, 3 on any SSH/transport error. **Never called from tests.**
- `init`: writes `~/.claude/fnb58-power.json` from
  `docs/examples/fnb58-power.example.json` if the target does not
  exist. With `--path` writes elsewhere (used by tests).

### scripts/install_skills.py

```
usage: install_skills.py [--target user|project|PATH] [--uninstall] [--dry-run]
```

- `--target user` (default): installs to `~/.claude/skills/power-*` so
  the Skills work from any directory on this machine.
- `--target project`: installs to `<repo>/.claude/skills/power-*`
  (normally a no-op since the repo already contains the source).
- `--target PATH`: install into an explicit directory (tests use this).
- Always a recursive copy (`shutil.copytree`), refreshed on re-run. In
  each copied SKILL.md, `python scripts/<X>.py` is rewritten to
  `<sys.executable> <repo>/scripts/<X>.py` so the skill works from any
  cwd (v3; symlink mode removed because a symlink can't carry the
  rewrite). Warns if that interpreter has neither `hid` nor `usb`.
  Legacy symlink installs pointing at this repo are replaced.
- Records a tiny `.installed-from` file in each copied skill dir
  pointing at the repo source path, so a subsequent `--uninstall` is
  safe (won't delete user-authored dirs).
- `--uninstall`: removes only the `power-*` skill dirs it previously
  installed (guarded by `.installed-from`; legacy symlinks unlinked).
- `--dry-run`: prints what it would do.
- Idempotent: running twice leaves the same result.

Cross-platform details:
- Line endings: skills are UTF-8 text — no CRLF rewriting needed, Git's
  `core.autocrlf` is upstream of us.

### scripts/power_sync.py (optional; ship if §12 still has time)

```
usage: power_sync.py --host ALIAS [--dry-run]
```

Thin wrapper around the tar-over-ssh pattern from the `test-system`
reference memory. Invokes:

```bash
tar --exclude=__pycache__ --exclude=*.egg-info --exclude=.pytest_cache \
    --exclude=runs --exclude=.venv --exclude=.git -cf - \
    src tests scripts scenarios pyproject.toml README.md CLAUDE.md \
  | ssh <ssh_alias> "cd <remote_repo> && tar -xf -"
```

Prints the tar argv on `--dry-run`. Does not restart the remote venv,
does not `pip install` — those stay manual. Exit 0 on success, 3 on
any SSH/tar failure. Skipped entirely if `scripts/power_sync.py` ends
up being cut for time; remote setup remains the manual process
documented in `docs/remote-setup.md`.

## 5. Scenario JSON schema

Required fields:

- `name: str` — short slug, must match filename stem.
- `description: str` — one-line human summary.
- `duration_s: number` — passed to `fnb58-log -d`.
- `expected_states: list[str]` — may be empty (`[]`).
- `budgets: { overall: {...}, per_state: {...} }` — may be empty objects.

Optional fields (with defaults):

- `repetitions: int` — default `1`. If > 1, writes stability rollup.
- `min_duration_s: number` — default `duration_s * 0.9`. Used by
  `power_validate` duration check.
- `marker_port: str | null` — default `null`. Accepts `"env:NAME"` to
  resolve from environment at run-time (empty → omit the flag).
- `marker_baud: int` — default `115200`.
- `regression: { max_<metric>_regression_pct: number, ... }` — default
  `{}`. Whitelisted metrics: `mean_mA`, `median_mA`, `p95_mA`,
  `max_mA`, `mean_mW`, `energy_mWh`, `charge_mAh`.
- `build_cmd: str | null` — used only by `power_optimize`.
- `flash_cmd: str | null` — used only by `power_optimize`.
- `default_host: str | null` — preferred host alias for this scenario.
  Used when `--host` is not given and no env / config default wins.
  May be `null`/absent for local-only scenarios. Host paths
  (e.g. `marker_port`) are resolved on the executing host: if
  `default_host` is set, `"/dev/ttyUSB0"` refers to the remote's
  `/dev/ttyUSB0`, not the local box's.

Budget sub-shape:

```json
"budgets": {
  "overall":   { "max_mean_mA": 0.10, "max_energy_mWh": 0.20 },
  "per_state": { "DEEP_SLEEP": { "max_p95_mA": 0.08 } }
}
```

Keys in `overall` and each `per_state.<STATE>` must start with `max_`
and match a known metric. Unknown keys are a scenario-load error
(exit 2). This forces the budget spec to fail loudly rather than
silently ignore typos like `max_mean_ma`.

## 6. runs/index.jsonl schema

Append-only. One JSON object per line, separators `(",", ":")`. Never
modified after write.

```json
{"csv":"runs/<sha>-deep_sleep-r1.csv","summary_json":"runs/<sha>-deep_sleep-r1.summary.json","meta_json":"runs/<sha>-deep_sleep-r1.meta.json","sha":"abcdef0123","branch":"main","scenario":"deep_sleep","label":"deep_sleep","timestamp_utc":"2026-04-23T12:34:56Z","repetition_index":1}
```

`runs/` is git-ignored except for `runs/.gitkeep`; `runs/index.jsonl`
is ignored too (local scratch).

## 7. Validation policy

`power_validate` exits non-zero iff at least one check fails. The order
of checks is fixed (see §4) so the JSON `checks` array is stable across
invocations — useful for diffing validate outputs across commits.

Exit 0 requires ALL of:

- duration ≥ scenario.min_duration_s
- every expected state observed at least once
- every `budgets.overall.max_*` respected
- every `budgets.per_state.<S>.max_*` respected
- every `regression.max_*_regression_pct` respected (or no baseline and
  scenario omits regression; absent baseline + requested regression = fail)

`--json` is additive: it changes stdout format, not the exit code.

## 8. Test project plan (hardware-free harness)

Location: `tests_e2e/` (pytest-collected; runnable via `bash tests_e2e/run.sh`).

### Fixtures (`tests_e2e/fixtures/`)

`gen_fixtures.py` generates three CSVs deterministically. Commits both
the generator AND the generated files so the test doesn't depend on
re-running the script.

- `good_deep_sleep.csv` — 3000 rows @ 10 ms, V=3.3, I ramp that satisfies
  `deep_sleep.json` (mean ≈ 0.05 mA overall, p95 ≈ 0.07 mA in DEEP_SLEEP
  state). Includes a 200 ms BOOT prefix and a single DEEP_SLEEP region
  covering the rest.
- `regressed_deep_sleep.csv` — identical structure, but current is 1.5×
  higher in DEEP_SLEEP → fails regression check against `good`.
- `missing_state.csv` — identical to `good` but the state column is
  empty throughout → fails the `required_states` check.

Energy_Ws / capacity_As columns are computed by the generator via the
same integrator logic the real logger uses (accumulating `power * 0.01`
per row) so the `compare.py` "cumulative column" branch works.

### test_power_skill.py

Each test uses `tmp_path` and passes `--runs-dir tmp_path / "runs"` to
every wrapper. The repo's real `runs/` is never touched.

1. `test_power_run_replay_writes_expected_files` — run
   `scripts/power_run.py --scenario deep_sleep.json --replay good_...csv
   --runs-dir <tmp>`. Assert csv/summary.json/meta.json appear with the
   `<sha>-deep_sleep-r1.*` naming, assert `runs/index.jsonl` gained one
   line, assert exit 0.
2. `test_power_validate_passes_good_against_good_baseline` — write a
   baseline pointer to the good stability.json in tmp, call validate
   against `good_deep_sleep.csv`, assert all checks pass and exit 0.
3. `test_power_validate_flags_regression` — baseline=good stability,
   candidate=regressed CSV, assert exit 1 and the failures list
   contains `regression.mean_mA`.
4. `test_power_validate_flags_missing_required_state` — candidate=
   `missing_state.csv`, assert exit 1 and failures contains
   `required_states`.
5. `test_power_baseline_overwrites_and_prints_previous` — call twice
   with two different runs; assert second call prints the first
   pointer's JSON to stderr and exits 0.
6. `test_power_compare_resolves_labels_by_newest_mtime` — drop two
   CSVs with different mtimes, call `power_compare <label> <label>`,
   assert the newer one wins AND the printed table matches what
   `scripts.compare.py` would print on the same two files.
7. `test_index_jsonl_is_append_only` — capture index content, call
   power_run again, assert first N lines are byte-identical and only
   new lines were appended.
8. `test_host_resolution_precedence` — build a synthetic config file,
   drive `resolve_host` through all four precedence levels (CLI, env,
   scenario, cfg default), and assert the right entry wins each time.
   Pure-Python, no SSH.
9. `test_power_run_host_argv_dry_run` — call `power_run.py --host test-pi
   --replay ... --host-config <fake.json> --dry-run` and assert the
   captured ssh argv list matches the expected shape (ssh alias, remote
   command, temp CSV path). No actual SSH. This proves the remote-exec
   code path is wired end-to-end without needing a real remote host.
10. `test_install_skills_idempotent` — run `install_skills.py
    --target <tmp>` twice, assert second call is a no-op,
    assert `.installed-from` sentinel exists, then `--uninstall` and
    assert skill dirs are gone while unrelated sibling dirs are
    preserved.
11. `test_power_host_list_with_no_config` — no config file anywhere;
    `power_host.py list --json` prints `{"hosts":{},"default_host":null}`
    and exits 0 (local-only mode is valid).

### tests_e2e/run.sh

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "[tests_e2e] running pytest..."
python -m pytest tests_e2e -v
echo "=================================="
echo "[tests_e2e] ALL CHECKS PASSED"
```

## 9. Migration of claude-commands/*.md

Prepend this banner (exact text) to each of `measure.md`, `compare.md`,
`baseline.md`:

```
> **DEPRECATED.** This prompt template is superseded by the
> `/power-<name>` Skill under `.claude/skills/`. It is retained here
> as historical reference and will be removed once external users have
> migrated. Do not add features here.
```

Update `claude-commands/README.md` to add a top paragraph pointing at
`.claude/skills/`, listing the `/power-*` commands with a one-line
description each, and noting that the legacy `/measure` / `/compare` /
`/baseline` still work but are frozen.

Do NOT touch the rest of those files in this round — they stay as-is
under the banner.

## 10. Hooks (Phase 5)

Single `UserPromptSubmit` hook registered in `.claude/settings.json`.
Runs a short inline bash command that:

- inspects `$CLAUDE_USER_PROMPT` for one of `/power-validate`,
  `/power-optimize`, `/power-measure`,
- if matched AND `git status --porcelain` is non-empty, prints
  `"note: working tree is dirty — run will be labeled -dirty"` to
  stdout (so Claude sees it) with exit 0.

Explicitly forbidden:

- hooks that trigger hardware
- hooks that modify files
- hooks that block submission (always exit 0)

## 11. Risks and mitigations

| Risk | Mitigation |
|------|-----------|
| FNB58 wedge from back-to-back writes during repetitions | `power_run.py` sleeps 1.0 s between runs; spawns `fnb58-log` as a subprocess so the transport is fully closed between reps (matches the CLAUDE.md gotcha). |
| Replay path silently diverges from real-device path | Replay reuses the real `SummaryAccumulator` — no duplicate math. Adds a `replay: <path>` field to meta.json so any consumer can tell the two apart. |
| Scenario schema drift vs validator | `load_scenario` rejects unknown keys under `budgets.overall` / `budgets.per_state.*` / `regression`. Scenarios that "silently pass" because of typos are treated as bugs. |
| `scripts/compare.py` import-as-library breaks if that file grows a top-level `sys.exit` | `scripts/__init__.py` is NOT added. Instead, `_power_common` uses `importlib.util.spec_from_file_location` to load `compare.py` as a module, isolated from the `__main__` guard (that guard is already correct in `compare.py`). |
| `runs/index.jsonl` grows unbounded | Out of scope this round. Documented in §12 as future work. |
| Windows `\n` vs `\r\n` line endings in `jsonl` | Force `"\n"` on writes (binary append mode via `open(..., "a", encoding="utf-8", newline="\n")`). |
| Git-sha detection in non-git test dirs | `git_sha()` returns `"nogit"` cleanly — tests rely on this. |
| FNB58 disconnect mid-repetition | `power_run.py` exits 3 on first failure; the N-1 stability rollup is not written. Meta sidecars for completed repetitions remain as evidence. |
| SSH alias not in `~/.ssh/config` on the invoking machine | `power_host probe` surfaces the failure early. `power_run --host X` on an unresolvable alias exits 3 with `ssh: could not resolve hostname` passed through to stderr. No implicit fallback to local — silent host-swap would be worse than a failure. |
| Remote repo drift (remote has an older `fnb58-log` than local code) | Out of band: `scripts/power_sync.py` handles this if shipped; otherwise documented in `docs/remote-setup.md`. Meta sidecar records the host alias so post-hoc diagnosis is possible. The `fnb58-log --version` output on the remote could be recorded in meta — added to the plan as a nice-to-have, not a gate. |
| Summary JSON mixed with fnb58-log's own stderr over SSH | `fnb58-log -o <file> --summary` puts the summary on **stdout** (per `__main__.py:177-182` when `writing_to_file` is true); stderr carries device info and warnings. We capture stdout cleanly via `ssh ... "cmd"`. Verified by reading `__main__.py` before finalizing this plan. |
| Installed skills can't find `scripts/` outside the logger repo | (v3) installer always copies and rewrites `python scripts/power_*.py` to absolute paths; `test_install_skills_rewrites_script_paths_for_other_repos` runs the rewritten command from an unrelated cwd. |
| Host config file contains secrets (some users add passphrases or paths) | Config lives under `~/.claude/` by default and is never committed. The example at `docs/examples/fnb58-power.example.json` contains no secrets. `.claude/fnb58-power.local.json` is git-ignored. |

## 12. Acceptance criteria

- [ ] `docs/plans/power-skill.plan.md` approved by user before code written.
- [ ] `python -m pytest` (all of `tests/` + `tests_e2e/`) is green.
- [ ] `ruff check src tests scripts tests_e2e` is clean.
- [ ] `bash tests_e2e/run.sh` exits 0 and prints the PASS banner.
- [ ] `/power-measure deep_sleep --replay tests_e2e/fixtures/good_deep_sleep.csv`
      produces the expected files under `runs/` and prints a PASS summary.
- [ ] `/power-validate deep_sleep <regressed_csv>` exits non-zero and the
      skill surfaces the failing checks verbatim.
- [ ] `git diff src/` fits on one screen (ideally empty).
- [ ] `scripts/install_skills.py --target <tmp>` produces a usable
      skill tree (copies with absolute script paths); `--uninstall` cleanly
      reverses it; second install is a no-op.
- [ ] `scripts/power_host.py list` on a bare machine (no config file)
      exits 0 and prints an empty host set.
- [ ] Host resolution precedence (CLI > env > scenario > cfg default)
      passes its unit test.
- [ ] `power_run.py --host test-pi --replay ... --dry-run` prints an
      ssh argv of the expected shape — proves the remote branch is
      wired without needing a real SSH host for CI.
- [ ] `docs/remote-setup.md` includes a copy-paste one-time setup
      (SSH key, tar-over-ssh, venv, udev rule) that reproduces the
      `test-system` rig.
- [ ] §1 "non-goals" matches what was actually not built — update here
      if something slipped.

## 13. Future MCP server stub (NOT implemented this round)

Planned tool shapes (paraphrased so future-me has something to refer
back to): `measure_power(scenario, label?) -> {paths, summary}`;
`validate_power_budget(scenario, candidate, baseline?) -> {pass, checks,
failures}`; `compare_runs(a, b, state?) -> {table, delta}`;
`list_runs(branch?, scenario?, since?) -> [index_entries]`. The server
would import the same `_power_common` helpers and invoke the wrapper
mains in-process to keep behavior identical. Scope TBD in a separate
plan file.

## 14. Optional stretch: `/power-plot`

If all acceptance criteria pass with time remaining, add
`scripts/power_plot.py` (stdlib + matplotlib if already available,
otherwise skipped) that renders a current-vs-time PNG next to the CSV.
A thin `power-plot` skill invokes it. Not required for sign-off; does
not gate other phases.

## 15. Multi-machine skill installation

The user develops from several machines (Windows 11 + Linux + macOS).
Skills must be trivially portable with no per-machine manual editing.

### Source of truth

`.claude/skills/power-*/` in the repo is the one source of truth. Every
other copy is derived. This means:

- `git clone` + `git pull` already syncs skills between dev machines
  that have the repo.
- Changes go through PR review the same as code changes.

### Two install targets

1. **Project-scoped** (zero effort): the repo's `.claude/skills/`
   directory is picked up by Claude Code when it's run with the repo
   as the working directory. Nothing to do.
2. **User-scoped** (optional, for invoking from other repos / other
   cwd): `scripts/install_skills.py --target user` mirrors the skills
   into `~/.claude/skills/`. Idempotent; reversible via `--uninstall`.

### Install mode (v3)

Copy only, on every OS. The v2 symlink mode was removed: the repo's
SKILL.md files use `python scripts/power_*.py`, which only resolves
with the logger repo as cwd, so user-scoped copies must have those
commands rewritten to absolute paths — impossible through a symlink.
Re-run the installer after `git pull` to refresh.

### Uninstall safety

Every installed skill dir gets a small `.installed-from` file with the
absolute source path and a timestamp. `--uninstall` refuses to delete
any skill dir that doesn't have this file, so hand-authored skills
next door are always safe.

### Workflow per machine

```bash
# First time on a new machine:
git clone <repo> && cd <repo>
python -m pip install -e ".[dev]"
python scripts/install_skills.py          # optional: user-global

# After a pull that changed a skill body (copy mode only):
python scripts/install_skills.py          # refresh user-global mirror
```

This is documented in `.claude/README.md` alongside the single-host
"just clone and go" path.

## 16. Remote FNB58 access over SSH

### Motivation

The user has a permanent FNB58 setup on a Raspberry Pi reachable via
SSH (alias `test-system`, memory: `reference_test_system.md`). They
want to drive measurements from their Windows dev box without
physically moving the meter.

### Design

Remote execution is a per-invocation choice. Everything else stays
local: scenario parsing, validation, indexing, reporting. Only the
actual `fnb58-log` invocation is shipped off-box.

### Host config file

Location lookup order (first hit wins):

1. `$FNB58_POWER_HOST_CONFIG` env var (absolute path).
2. `<repo>/.claude/fnb58-power.local.json` (git-ignored, per-project
   override).
3. `~/.claude/fnb58-power.json` (user-global).

Shape:

```json
{
  "default_host": "test-pi",
  "hosts": {
    "test-pi": {
      "ssh_alias": "test-system",
      "remote_repo": "~/fnb58-logger",
      "fnb58_log_cmd": "~/fnb58-logger/.venv/bin/fnb58-log",
      "remote_tmp_dir": "/tmp",
      "python_cmd": "~/fnb58-logger/.venv/bin/python"
    },
    "lab-linux": {
      "ssh_alias": "lab-linux",
      "remote_repo": "~/work/fnb58-logger",
      "fnb58_log_cmd": "fnb58-log",
      "remote_tmp_dir": "/tmp"
    }
  }
}
```

Required fields per host: `ssh_alias`, `fnb58_log_cmd`. Optional:
`remote_repo` (used by `power_sync.py`), `remote_tmp_dir` (default
`/tmp`), `python_cmd` (used when we need to run a helper module on
the remote; defaults to the `fnb58_log_cmd`'s venv python).

SSH auth, hostnames, ports, jump hosts — all delegated to `~/.ssh/config`.
The user's key agent handles credentials. We never touch them.

### Precedence for choosing a host

`resolve_host` in `_power_common.py` picks in this order:

1. `--host <alias>` CLI flag. `--host local` forces local execution
   even if a default is set.
2. `$POWER_HOST` environment variable.
3. `scenario.default_host` from the scenario JSON.
4. `cfg.default_host` from the config file.
5. `None` → local execution.

This lets a scenario pin itself to a rig (e.g. `radio_tx.json` only
makes sense with the antenna harness), while leaving everyday
scenarios to follow the machine-level default.

### Execution flow (per run)

Given resolved host `H` and a computed local CSV path `L`:

1. Compute `remote_csv = <H.remote_tmp_dir>/fnb58-<uuid>.csv`.
2. Build argv:
   ```
   ssh <H.ssh_alias> -o BatchMode=yes -o ServerAliveInterval=15 \
     -- <H.fnb58_log_cmd> -o <remote_csv> -d <duration> --summary \
        [--marker-port ...] [--marker-baud ...]
   ```
3. Spawn via `subprocess.Popen`. Capture stdout into the local
   `<L.summary_json>`. Tee stderr to our own stderr.
4. On exit 0: pull CSV back with `scp <ssh_alias>:<remote_csv> <L>`
   (or equivalently `ssh <ssh_alias> cat <remote_csv> > <L>` — pick
   `scp` because it preserves mtime and surfaces transfer errors).
5. Cleanup: `ssh <ssh_alias> rm -f <remote_csv>`. Failure here is
   warned, not fatal (remote tmp will clear on reboot).
6. Non-zero exit from step 3 → exit 3 with the captured stderr passed
   through.

### What lives where

| Artifact                    | Location |
|-----------------------------|----------|
| `fnb58-log` CLI             | Remote venv (pre-installed)            |
| CSV during capture          | Remote `<remote_tmp_dir>/fnb58-*.csv`  |
| CSV after capture           | Local `runs/<sha>-<label>-r<n>.csv`    |
| Summary JSON                | Local (captured over SSH stdout)       |
| Scenario files              | Local (source-of-truth)                |
| Baseline pointer            | Local `runs/.baseline-<branch>.json`   |
| Index                       | Local `runs/index.jsonl`               |

The remote stores nothing durable. Every run's artifacts live on the
dev box that invoked it. This keeps the remote stateless and means
different dev boxes can share one FNB58 rig without coordinating.

### Markers over SSH

`marker_port` in the scenario refers to the **remote's** serial
device (e.g. `/dev/ttyUSB0`) — `fnb58-log` runs on the remote and
opens that port directly. No tunneling needed. The remote must have
`pyserial` installed in its venv if scenarios use markers.

### Code sync (`scripts/power_sync.py`)

Optional, ships only if time allows. Thin wrapper around the
established tar-over-ssh pattern from the `test-system` memory:

```bash
tar --exclude=__pycache__ --exclude=*.egg-info --exclude=.pytest_cache \
    --exclude=runs --exclude=.venv --exclude=.git -cf - \
    src tests scripts scenarios pyproject.toml README.md CLAUDE.md \
  | ssh <ssh_alias> "cd <remote_repo> && tar -xf -"
```

Does not reinstall the package, does not touch the venv. Users run
`pip install -e .` on the remote manually when `pyproject.toml`
dependencies change. This keeps `power_sync.py` fast and predictable.

### Tests (hardware-free)

The full SSH path is not exercised by pytest (no remote fixture).
What IS tested:

- host-config lookup order + precedence rules (pure Python, fake
  file).
- `power_run.py --host <alias> --replay <csv> --dry-run` captures
  the ssh argv in a subprocess-mockable seam (a single
  `scripts._power_common.run_subprocess` indirection) and asserts
  the shape. This proves the remote branch is wired without needing
  a live remote.
- `power_host.py list` on a bare machine (no config) exits 0.

Actual end-to-end remote runs are a documented manual step in
`docs/remote-setup.md` — same smoke test from the `test-system`
memory, now invoked via `/power-measure deep_sleep --host test-pi`.

### `.claude/settings.json` additions

None. The one existing hook (dirty-tree warning) already covers
remote runs — a dirty local tree still produces a `-dirty` suffix
because the git SHA is computed locally.

### Acceptance for this section

See §12 items marked as SSH-related. None require a live SSH host.
