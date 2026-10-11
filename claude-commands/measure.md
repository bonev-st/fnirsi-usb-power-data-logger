---
description: Record a labeled power-measurement run with the FNB58.
argument-hint: <label> [duration_s] [extra fnb58-log args]
---

> **DEPRECATED.** This prompt template is superseded by the
> `/power-measure` Skill under `.claude/skills/`. It is retained here
> as historical reference and will be removed once external users have
> migrated. Do not add features here.


Record a power-measurement run and save it under `runs/` with a filename
that ties the capture to the current git state.

## What to do

1. Parse the arguments. The first arg is a short `<label>` (e.g.
   `baseline`, `deep-sleep-v2`, `pr123-before`). The second optional arg
   is `<duration_s>` — default to **30** if absent. Any remaining args
   are passed through to `fnb58-log` verbatim (so the user can add
   `--marker-port COM5 --marker-baud 115200` etc.).

2. Resolve the git SHA of the current tree:
   ```bash
   git rev-parse --short=10 HEAD
   ```
   If the working tree is dirty (`git status --porcelain` non-empty),
   append `-dirty` to the SHA. If not a git repo, use `nogit`.

3. Create the output dir if missing:
   ```bash
   mkdir -p runs
   ```

4. Run the logger. Save CSV to `runs/<sha>-<label>.csv` and emit the
   summary JSON to `runs/<sha>-<label>.summary.json`:
   ```bash
   fnb58-log \
     -o runs/<sha>-<label>.csv \
     -d <duration_s> \
     --summary \
     [pass-through args] \
     > runs/<sha>-<label>.summary.json
   ```

5. Write a sidecar `runs/<sha>-<label>.meta.json` containing:
   - `label`, `sha`, `dirty` (bool), `duration_s`, `timestamp_utc` (ISO-8601)
   - `branch` from `git rev-parse --abbrev-ref HEAD`
   - `extra_args` — the pass-through args, if any
   - `hostname` (from `hostname`) and `target_board` (from env
     `TARGET_BOARD` if set)

6. After the run, print the summary JSON contents formatted for humans —
   mean mA, p95 mA, energy mWh, duration. Remind the user how to
   compare: `/compare runs/<sha>-<label>.csv <other>.csv`.

## Conventions

- Never overwrite an existing `runs/*.csv`. If it already exists, append
  `-N` where N is the smallest integer that makes the filename unique.
- If `fnb58-log` fails (no device connected, permissions), surface the
  error and do not create the meta file.
- Prefer the default `hid` backend. Only switch to `--backend pyusb` if
  the user requests it or the hid path fails.
