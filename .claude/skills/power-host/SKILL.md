---
name: power-host
description: Inspect or probe the FNB58 remote-host configuration used by /power-measure --host.
argument-hint: list | show <alias> | probe <alias> | init
---

Inspect the user's FNB58 host-config file and, optionally, check that
the configured SSH alias is actually reachable.

## What to do

1. Pick the subcommand from the first arg: `list`, `show`, `probe`, or
   `init`.

2. Invoke the wrapper:
   ```bash
   python scripts/power_host.py <subcommand> [<alias>] [--json]
   ```

3. For `list` — show the resolved hosts; flag any whose `ssh_alias`
   does not appear in `~/.ssh/config` (the wrapper fills in
   `ssh_alias_in_ssh_config`). Tell the user they'll need to add an
   SSH alias before `/power-measure --host <that-alias>` works.

4. For `show <alias>` — dump the resolved entry verbatim.

5. For `probe <alias>` — the wrapper runs a real SSH command
   (`BatchMode=yes`, `ConnectTimeout=5`, `test -x <fnb58_log_cmd>`).
   Exit 0 means reachable AND `fnb58-log` exists at the configured
   path. Report the result; do not retry on your own.

6. For `init` — the wrapper copies the template config to
   `~/.claude/fnb58-power.json` (or `--path`). Tell the user which
   fields to edit (`ssh_alias`, `fnb58_log_cmd`).

## Rules (apply verbatim on every invocation)

- **Do not compute pass/fail yourself. Trust the wrapper's exit code.**
  `probe` exits 3 on any SSH failure — do not interpret; surface the
  stderr.
- **Never edit scripts/, src/, or scenarios/ from within this skill —
  only read and invoke.** This includes the config file: offer the
  init template and let the user edit it.
- **Surface the exact JSON result and the run file paths so the user
  can open them.** Include the config-file path the wrapper reported
  in its output (field `config_path`).
