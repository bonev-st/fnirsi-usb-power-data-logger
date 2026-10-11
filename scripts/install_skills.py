#!/usr/bin/env python3
"""Install .claude/skills/power-* into ~/.claude/skills/ (or another target).

The repo's SKILL.md files call ``python scripts/power_*.py``, which only
resolves when Claude Code runs inside this repo. Installed copies are used
from other repos (e.g. a firmware project), so each copied SKILL.md has
those commands rewritten to this interpreter + this repo's absolute
``scripts/`` path. ``scenarios/`` and ``runs/`` stay relative, so they (and
the git SHA) still come from the repo Claude is working in.

Installs are always copies (a symlink cannot carry the rewritten paths);
re-run after ``git pull`` to refresh them. Legacy symlink installs pointing
at this repo are replaced by copies.

Each installed skill dir gets a ``.installed-from`` sentinel naming the source
path. ``--uninstall`` only removes dirs carrying that sentinel, so any
hand-authored skills next door are safe.

Exit codes
----------
0 success, 2 usage error.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _power_common import EXIT_OK, EXIT_USAGE, die, utc_timestamp  # noqa: E402


SENTINEL = ".installed-from"
SKILL_PREFIX = "power-"

# "python scripts/power_run.py" -> "<sys.executable> <repo>/scripts/power_run.py"
_SCRIPT_CMD_RE = re.compile(r"\bpython (scripts/[A-Za-z0-9_]+\.py)\b")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="install_skills.py",
        description="Install .claude/skills/power-* into a target skills dir.",
    )
    p.add_argument("--target", default="user",
                   help="'user' (default) ~/.claude/skills, 'project' "
                        "<repo>/.claude/skills, or an explicit PATH")
    p.add_argument("--uninstall", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_target(arg: str) -> Path:
    if arg == "user":
        return Path.home() / ".claude" / "skills"
    if arg == "project":
        return _project_root() / ".claude" / "skills"
    return Path(arg)


def _shell_path(path: Path | str) -> str:
    text = Path(path).as_posix()
    return f'"{text}"' if " " in text else text


def _rewrite_skill_md(text: str) -> str:
    """Point ``python scripts/X.py`` at this interpreter + repo."""
    python = _shell_path(sys.executable)
    scripts = _project_root() / "scripts"
    return _SCRIPT_CMD_RE.sub(
        lambda m: f"{python} {_shell_path(scripts / Path(m.group(1)).name)}",
        text)


def _list_source_skills() -> list[Path]:
    src = _project_root() / ".claude" / "skills"
    if not src.is_dir():
        return []
    return sorted(p for p in src.iterdir()
                  if p.is_dir() and p.name.startswith(SKILL_PREFIX))


def _install_one(src: Path, dst: Path, dry_run: bool) -> str:
    if dry_run:
        return f"WOULD copy: {src} -> {dst}"
    if dst.is_symlink():
        # Legacy symlink install: replace with a rewritten copy if it's ours.
        if Path(os.readlink(dst)).resolve() != src.resolve():
            return f"skip  : {dst} (symlink to another source; remove manually)"
        dst.unlink()
    elif dst.exists():
        if not (dst / SENTINEL).is_file():
            return f"skip  : {dst} (exists and not managed by us)"
        try:
            with (dst / SENTINEL).open("r", encoding="utf-8") as f:
                info = json.load(f)
        except (json.JSONDecodeError, OSError):
            return f"skip  : {dst} (sentinel unreadable; remove manually)"
        if Path(info.get("source", "")).resolve() != src.resolve():
            return f"skip  : {dst} (different source recorded; remove manually)"
        # Refresh the copy in case content changed.
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    skill_md = dst / "SKILL.md"
    if skill_md.is_file():
        text = skill_md.read_text(encoding="utf-8")
        skill_md.write_text(_rewrite_skill_md(text), encoding="utf-8",
                            newline="\n")
    sentinel = dst / SENTINEL
    sentinel.write_text(
        json.dumps({"source": str(src), "installed_at_utc": utc_timestamp()},
                   indent=2) + "\n",
        encoding="utf-8",
    )
    return f"copy  : {src} -> {dst}"


def _uninstall_one(dst: Path, dry_run: bool) -> str:
    if not dst.exists() and not dst.is_symlink():
        return f"absent: {dst}"
    if dst.is_symlink():
        if dry_run:
            return f"WOULD unlink: {dst}"
        dst.unlink()
        return f"unlink: {dst}"
    sentinel = dst / SENTINEL
    if not sentinel.is_file():
        return f"skip  : {dst} (no {SENTINEL}; not installed by us)"
    if dry_run:
        return f"WOULD rmtree: {dst}"
    shutil.rmtree(dst)
    return f"rm    : {dst}"


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    target = _resolve_target(args.target)
    target.mkdir(parents=True, exist_ok=True)

    sources = _list_source_skills()
    if not sources:
        die(EXIT_USAGE,
            f"no power-* skills found under {_project_root() / '.claude/skills'}; "
            f"create them first (Phase 11)")

    if args.uninstall:
        for src in sources:
            dst = target / src.name
            print(_uninstall_one(dst, args.dry_run))
        return EXIT_OK

    for src in sources:
        dst = target / src.name
        print(_install_one(src, dst, args.dry_run))

    # The rewritten skills run under this interpreter, so it must be able
    # to talk to the FNB58.
    if not any(importlib.util.find_spec(m) for m in ("hid", "usb")):
        print(f"warning: {sys.executable} has neither 'hid' nor 'usb' "
              f"installed; /power-measure will not be able to open the FNB58. "
              f"Re-run this installer with the Python you used for "
              f"`pip install -e .`", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
