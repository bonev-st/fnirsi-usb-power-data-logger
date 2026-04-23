# Claude Code slash commands

Three prompt templates Claude Code can load as slash commands:

| Command      | What it does                                     |
|--------------|--------------------------------------------------|
| `/measure`   | Record a labeled power run, save CSV + summary + meta under `runs/`. |
| `/compare`   | Diff two runs via `scripts/compare.py` and explain the delta. |
| `/baseline`  | Pin the current run as the baseline for this branch. |

## Install (one-time, per machine)

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
