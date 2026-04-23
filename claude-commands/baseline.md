---
description: Mark the current fnb58-log run as the baseline for this branch.
argument-hint: [run_csv]
---

Record the "baseline" pointer for the current branch so subsequent
`/compare` calls can default one side.

## What to do

1. If the user passed a CSV path, use it. Otherwise pick the newest
   `runs/*.csv` (by mtime).

2. Resolve the current branch:
   ```bash
   git rev-parse --abbrev-ref HEAD
   ```
   If detached HEAD or not a git repo, ask the user for a label.

3. Write (or overwrite) `runs/.baseline-<branch>.json` with:
   - `csv`: absolute path to the chosen CSV
   - `branch`
   - `set_at_utc`: ISO-8601 timestamp
   - If a sibling `.meta.json` exists for the chosen CSV, inline its
     `sha`, `dirty`, `label` fields here for convenience.

4. Print the new baseline's summary (read the sibling
   `*.summary.json` if present, otherwise run
   `python scripts/compare.py <csv> <csv>` with both args the same to
   produce the metrics table).

5. Tell the user they can now run `/compare <other.csv>` with one arg
   and the command should resolve the missing side from
   `runs/.baseline-<branch>.json`. (That resolution lives in
   `compare.md`'s behaviour — keep the two commands consistent if the
   user asks to change this convention.)

## Conventions

- This is purely a bookkeeping command. It does not start a new
  measurement — use `/measure` for that.
- Never delete an existing baseline pointer silently; if overwriting,
  print the previous value first.
