---
name: power-validate
description: Validate an FNB58 run against a scenario's budgets, required states, and regression vs baseline.
argument-hint: <scenario> <candidate> [--baseline B]
---

Validate a candidate run against a scenario's budgets and regression
rules, relative to the branch baseline.

## What to do

1. Resolve `<scenario>` to `scenarios/<name>.json` (bare name) or a
   direct path.

2. Resolve `<candidate>` to either an absolute path the user provided,
   or — if it's a bare label — let the wrapper figure it out. If the
   user clearly gave a label (e.g. "last deep_sleep run"), glob
   `runs/*-<label>*.csv` first and pass the newest match explicitly.

3. Invoke the wrapper:
   ```bash
   python scripts/power_validate.py --scenario scenarios/<name>.json \
       --candidate <candidate.csv-or-stability.json> \
       [--baseline <path>] \
       --json
   ```

4. Parse the JSON report. If `pass: true`, say PASS and show the table
   of checks. If `pass: false`, list every entry of `failures` verbatim
   — do not soften, do not speculate why, do not say "probably fine,
   close enough." The exit code is authoritative.

5. If the wrapper reports no baseline is available and the scenario
   demanded a regression check, tell the user to run
   `/power-baseline` first.

## Rules (apply verbatim on every invocation)

- **Do not compute pass/fail yourself. Trust the wrapper's exit code.**
  `power_validate.py` exit 0 = pass, 1 = failure, 2 = usage error.
- **Never edit scripts/, src/, or scenarios/ from within this skill —
  only read and invoke.** Especially: do not "fix" failing runs by
  editing the scenario's budget numbers. A budget breach is feedback.
- **Surface the exact JSON result and the run file paths so the user
  can open them.** Quote the `failures` list verbatim.
