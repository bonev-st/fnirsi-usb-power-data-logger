---
name: power-measure
description: Record a labeled FNB58 power run for a scenario, save CSV + summary.json + meta.json under runs/.
argument-hint: <scenario> [--label L] [--host ALIAS] [--replay PATH] [--target-board B] [--meta K=V]
---

Record a power-measurement run for the named scenario and save artifacts
under `runs/` with names tied to the current git state.

## What to do

1. Take the first positional arg as `<scenario>` — either a bare name
   resolved against `scenarios/<name>.json`, or a direct path to a
   scenario JSON file.

2. Forward everything to the wrapper. The wrapper owns all side effects.
   ```bash
   python scripts/power_run.py --scenario scenarios/<name>.json \
       [--label <L>] [--host <ALIAS>] [--replay <PATH>] \
       [--target-board <B>] [--meta <K=V> ...]
   ```
   Pass `--marker-port` / `--marker-baud` through if the user supplied
   them. Likewise `--target-board <NAME>` and `--meta KEY=VALUE`
   (repeatable): use them to record what is actually on the DUT when it
   differs from the current git state, e.g. `--meta firmware=<sha>` after
   flashing an older build. Do not add flags the user didn't ask for.

3. Read the JSON that `power_run.py` prints on stdout. Surface it to
   the user along with the concrete paths from the returned object
   (`csv`, `summary_json`, `meta_json`, or `stability.json` when
   `repetitions > 1`).

4. Summarize the numbers in 2–3 sentences: mean mA, p95 mA, energy
   mWh, duration. Remind the user how to compare and validate next:
   `/power-compare <baseline> <this-run>` and `/power-validate <scenario> <this-run>`.

## Rules (apply verbatim on every invocation)

- **Do not compute pass/fail yourself. Trust the wrapper's exit code.**
  Non-zero exit means the run did not produce valid artifacts — say so
  and stop. Do not try to "fix" the output.
- **Never edit scripts/, src/, or scenarios/ from within this skill —
  only read and invoke.**
- **Surface the exact JSON result and the run file paths so the user
  can open them.** Don't paraphrase the JSON; show it.
