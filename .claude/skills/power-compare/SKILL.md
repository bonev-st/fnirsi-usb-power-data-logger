---
name: power-compare
description: Diff two FNB58 runs (by path or label) and explain the delta.
argument-hint: <baseline> <candidate> [--state NAME]
---

Diff two runs and explain what changed.

## What to do

1. Treat the two positional args as paths first. If either isn't an
   existing file, it's a label — the wrapper resolves it to the newest
   matching CSV under `runs/`.

2. Invoke:
   ```bash
   python scripts/power_compare.py <baseline> <candidate> [--state <NAME>]
   ```

3. Show the table verbatim. Then add a short analysis:
   - Mean mA, p95 mA, and energy mWh are the three metrics that matter
     most for power work. Median mA is the best steady-state indicator;
     p95 captures active-mode spikes.
   - Flag any change >5% as "significant".
   - Call out changes <1% as "within noise floor".
   - If `duration` differs by more than 1%, warn the user that total
     energy comparisons aren't like-for-like; point them at mean mW
     instead.

4. If a `.meta.json` sidecar exists for either run, load it and name
   the `sha`, `branch`, and `target_board` so the user can trace each
   number back to its commit.

## Rules (apply verbatim on every invocation)

- **Do not compute pass/fail yourself. Trust the wrapper's exit code.**
  `power_compare` is a reporting tool — it always exits 0.
- **Never edit scripts/, src/, or scenarios/ from within this skill —
  only read and invoke.**
- **Surface the exact JSON result and the run file paths so the user
  can open them.** Always show the raw table from `compare.py`. Don't
  summarize away the numbers.
