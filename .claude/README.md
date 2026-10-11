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

## Multi-machine workflow

`.claude/skills/` is the single source of truth — no per-machine edits.
`git clone` / `git pull` syncs skills between your dev boxes.

### Optional: install Skills user-globally

If you want `/power-*` available from repos other than this one, mirror
the skill tree into `~/.claude/skills/`:

```bash
python scripts/install_skills.py             # symlink on Unix, copy on Windows
python scripts/install_skills.py --mode copy  # force copy on Unix
python scripts/install_skills.py --uninstall  # reverses the install safely
```

The install is idempotent. Every installed skill dir gets an
`.installed-from` sentinel; `--uninstall` refuses to delete skills that
lack it, so your hand-authored skills next door are safe.

### Windows symlinks

On Windows without developer mode, `symlink` will fail and the installer
drops to `copy` automatically (the fallback prints one line of note).
You can force copy with `--mode copy`.

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
