---
name: power-baseline
description: Pin a run as the baseline for this branch so future /power-validate calls can regress against it.
argument-hint: [<run_csv_or_stability_json>]
---

Pin a run as the branch's reference point for regression checks. This
is bookkeeping; it doesn't start a measurement.

## What to do

1. If the user passed a path, use it. Otherwise let the wrapper pick
   the newest `runs/*.stability.json` (preferred) or `runs/*.csv`.

2. Invoke:
   ```bash
   python scripts/power_baseline.py [<run>] [--branch <B>]
   ```

3. The wrapper prints the new pointer JSON to stdout; if it overwrote
   a previous pointer, the previous one is printed to stderr first.
   Show both to the user — they want to see what moved.

4. Tell the user that subsequent `/power-validate <scenario> <candidate>`
   calls will use this pointer automatically (no `--baseline` needed).

## Rules (apply verbatim on every invocation)

- **Do not compute pass/fail yourself. Trust the wrapper's exit code.**
- **Never edit scripts/, src/, or scenarios/ from within this skill —
  only read and invoke.**
- **Surface the exact JSON result and the run file paths so the user
  can open them.** If the wrapper reported a previous pointer to
  stderr, quote that too — never silently overwrite.
