---
description: Diff two fnb58-log CSV runs and summarize the change.
argument-hint: <baseline.csv> <candidate.csv> [--state NAME]
---

> **DEPRECATED.** This prompt template is superseded by the
> `/power-compare` Skill under `.claude/skills/`. It is retained here
> as historical reference and will be removed once external users have
> migrated. Do not add features here.


Compare two power-measurement runs and explain the delta.

## What to do

1. Treat the first two positional args as CSV paths: `<baseline>` and
   `<candidate>`. If the user gives labels instead of paths (e.g.
   `baseline` `candidate`), resolve them by globbing `runs/*-<label>.csv`
   and using the most recent match.

2. Run:
   ```bash
   python scripts/compare.py <baseline> <candidate> [--state NAME]
   ```
   If the user wants a specific state (e.g. `deep-sleep`), pass
   `--state <NAME>` — this restricts the comparison to rows where the
   firmware marker matched.

3. Read the table output and produce a short analysis:
   - Is the candidate an improvement or regression? Look at `mean mA`,
     `p95 mA`, and `energy mWh` — those three matter most for power work.
     `median` is the best indicator of steady-state current; `p95` tells
     you about active-mode spikes.
   - Highlight any change >5% with an explicit "this looks significant"
     note. Changes <1% are noise — say so.
   - If `duration` differs by more than 1%, warn the user that total
     energy comparisons aren't like-for-like; point them at mean mW
     instead.

4. If a `runs/<sha>-<label>.meta.json` sidecar exists for either run,
   load it and include the `sha`, `branch`, and `target_board` in the
   analysis so the user can trace back which code produced which number.

5. Do NOT edit any code in this command — only read and report. If the
   user asks "what changed between these", that's a follow-up for a
   separate prompt, not this command.

## Conventions

- Keep the analysis concise — a few sentences plus the table. The user
  will ask follow-up questions.
- Always show the raw table from `compare.py`. Don't summarize away the
  numbers.
