# Claude Code slash commands

> **Superseded by Skills under `.claude/skills/power-*/`.** The prompt
> templates in this directory are retained as historical reference
> only. New functionality is added there, not here.
>
> Use the `/power-*` commands instead:
>
> | New command       | Replaces          | What it does                                                       |
> |-------------------|-------------------|--------------------------------------------------------------------|
> | `/power-measure`  | `/measure`        | Record a scenario run; save CSV + summary + meta; stability rollup |
> | `/power-validate` | *(new)*           | Gate a run against scenario budgets + regression vs branch baseline |
> | `/power-compare`  | `/compare`        | Diff two runs (by path or label) via `scripts/compare.py`          |
> | `/power-baseline` | `/baseline`       | Pin a run as the branch's reference point                          |
> | `/power-host`     | *(new)*           | Inspect/probe remote FNB58 host config (SSH aliases)               |
>
> See [`../.claude/README.md`](../.claude/README.md) for how to install
> the Skills user-globally and
> [`../docs/remote-setup.md`](../docs/remote-setup.md) for SSH setup.

---

## Legacy templates (historical)

Three prompt templates Claude Code could load as slash commands:

| Command      | What it does                                     |
|--------------|--------------------------------------------------|
| `/measure`   | Record a labeled power run, save CSV + summary + meta under `runs/`. |
| `/compare`   | Diff two runs via `scripts/compare.py` and explain the delta. |
| `/baseline`  | Pin the current run as the baseline for this branch. |

## Install (one-time, per machine) — legacy only

Claude Code looks for project slash commands under `.claude/commands/`.
Copy or symlink this directory there:

**Linux / macOS**
```bash
mkdir -p .claude
ln -s ../claude-commands .claude/commands
```

**Windows (PowerShell, developer mode on)**
```powershell
New-Item -ItemType Directory -Force .claude | Out-Null
New-Item -ItemType SymbolicLink -Path .claude\commands -Target ..\claude-commands
```

**Windows without symlink support** — just copy:
```powershell
New-Item -ItemType Directory -Force .claude\commands | Out-Null
Copy-Item claude-commands\*.md .claude\commands\
```

The source of truth stays `claude-commands/` (checked in). Re-copy after
pulls if you used the copy path instead of a symlink.

**You don't need this for the `/power-*` Skills** — those are at
`.claude/skills/` and are picked up automatically.
