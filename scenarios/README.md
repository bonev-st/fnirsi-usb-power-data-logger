# Scenarios

Each file here declares one measurable situation: how long to measure, which
firmware states to expect, what current/energy budget to enforce, and how
much regression is tolerable versus the branch baseline.

A scenario is consumed by `scripts/power_run.py` (to decide duration,
repetitions, and marker settings) and `scripts/power_validate.py` (to
decide pass/fail).

## Filename convention

`scenarios/<name>.json`. The `name` field inside the file MUST equal the
filename stem — the wrappers enforce this.

## Schema

```json
{
  "name": "deep_sleep",
  "description": "One-line human summary.",
  "duration_s": 30,
  "min_duration_s": 25,
  "repetitions": 3,
  "expected_states": ["DEEP_SLEEP"],
  "marker_port": "env:MARKER_PORT",
  "marker_baud": 115200,
  "budgets": {
    "overall":   { "max_mean_mA": 0.10, "max_energy_mWh": 0.20 },
    "per_state": { "DEEP_SLEEP": { "max_p95_mA": 0.08 } }
  },
  "regression": {
    "max_mean_mA_regression_pct": 3.0,
    "max_p95_mA_regression_pct": 8.0,
    "max_energy_mWh_regression_pct": 3.0
  },
  "default_host": null,
  "build_cmd": null,
  "flash_cmd": null
}
```

### Required fields

| Field | Type | Notes |
|-------|------|-------|
| `name` | string | Must equal the file stem. |
| `description` | string | One-line summary. |
| `duration_s` | number | Passed to `fnb58-log -d`. |
| `expected_states` | list[string] | Every name listed must appear in the CSV's `state` column. |
| `budgets` | object | `overall` and `per_state` sub-objects (each may be empty). |

### Optional fields (with defaults)

| Field | Default | Notes |
|-------|---------|-------|
| `repetitions` | `1` | If > 1, `power_run.py` writes a `.stability.json` rollup with mean/stdev/cv across reps. |
| `min_duration_s` | `duration_s * 0.9` | Used by `power_validate.py` duration check. |
| `marker_port` | `null` | Forwarded to `fnb58-log --marker-port`. Accepts the special form `"env:NAME"` to resolve from `$NAME` at run time. |
| `marker_baud` | `115200` | |
| `regression` | `{}` | Allowed keys: `max_<metric>_regression_pct` where `<metric>` is one of `mean_mA, median_mA, p95_mA, max_mA, mean_mW, median_mW, p95_mW, energy_mWh, charge_mAh`. |
| `build_cmd` | `null` | Used by `scripts/power_optimize.py` (shell command). |
| `flash_cmd` | `null` | Used by `scripts/power_optimize.py` (shell command). |
| `default_host` | `null` | Preferred host alias for this scenario (see `docs/remote-setup.md`). |

### Budget sub-shape

Keys in `budgets.overall` and every `budgets.per_state.<STATE>` must
start with `max_` and the rest must match a known metric. Unknown keys
are a scenario-load error — fail loud to catch typos like `max_mean_ma`.

Allowed budget metrics: `mean_mA, median_mA, p95_mA, min_mA, max_mA,
mean_mW, median_mW, p95_mW, energy_mWh, charge_mAh, mean_V, min_V,
max_V, duration_s`.

## Shipped scenarios

- [`deep_sleep.json`](deep_sleep.json) — DUT in DEEP_SLEEP; budget on
  mean + p95 current and energy.
- [`radio_tx.json`](radio_tx.json) — radio burst at 0 dBm; per-state
  budgets on RADIO_TX (p95, max) and IDLE (mean).

Add more by copying one of these and editing. A scenario is a plain
JSON file; nothing fancier.

## Usage

```bash
# Run the scenario and capture results
/power-measure deep_sleep

# Validate last-known candidate against budgets + regression
/power-validate deep_sleep <candidate.csv>
```

See [`../docs/plans/power-skill.plan.md`](../docs/plans/power-skill.plan.md)
for the authoritative design.
