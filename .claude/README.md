# .claude/ — Claude Code Skills + settings

Project-scoped Skills and a single submit-hook for the FNB58
power-measurement workflow.

## What's here

```
.claude/
├── README.md            (this file)
├── settings.json        single UserPromptSubmit hook: dirty-tree warning
└── skills/
    ├── power-measure/   record a scenario run
    ├── power-validate/  gate against budgets + regression
    ├── power-baseline/  pin the branch reference
    ├── power-compare/   diff two runs
    └── power-host/      inspect remote-host config
```

## How Skills are invoked

When Claude Code is launched with the repo as its working directory,
every `<skill>/SKILL.md` under `.claude/skills/` is loaded
automatically. Invoke them as `/power-<name> <args>`.

For a step-by-step walkthrough (install, firmware repo setup,
baseline → validate workflow, troubleshooting) see
[`../docs/power-skills-guide.md`](../docs/power-skills-guide.md).

## Multi-machine workflow

`.claude/skills/` is the single source of truth — no per-machine edits.
`git clone` / `git pull` syncs skills between your dev boxes.

### Optional: install Skills user-globally

If you want `/power-*` available from repos other than this one (e.g.
your firmware project), copy the skill tree into `~/.claude/skills/`.
Run the installer with the Python you used for `pip install -e .` —
the installed skills will run the scripts with that interpreter:

```bash
python scripts/install_skills.py              # install / refresh
python scripts/install_skills.py --dry-run    # preview
python scripts/install_skills.py --uninstall  # reverses the install safely
```

Installed copies have `python scripts/power_*.py` rewritten to this
interpreter and this repo's absolute `scripts/` path, so they work from
any working directory. `scenarios/`, `runs/` and the git SHA still come
from the repo Claude Code is running in — keep your scenarios in the
firmware repo and the runs are named after the firmware commit.

Installs are always copies (a symlink can't carry the rewritten paths):
**re-run the installer after `git pull`** to pick up skill changes.
Re-running is safe and refreshes managed copies. Every installed skill
dir gets an `.installed-from` sentinel; `--uninstall` refuses to delete
skills that lack it, so your hand-authored skills next door are safe.

## Remote FNB58 access over SSH

See [`../docs/remote-setup.md`](../docs/remote-setup.md) for the
one-time-per-host setup. Once the config exists, any `/power-measure`
or `/power-optimize` can target a remote host with `--host <alias>`,
or a host-pinned scenario with `default_host` set will pick it up
automatically. `--host local` forces local execution.

## Legacy slash commands (`/measure`, `/compare`, `/baseline`)

Superseded by the `/power-*` Skills. The old prompt templates under
`../claude-commands/` are retained for reference and carry a deprecation
banner; new features go only in the Skills here.
