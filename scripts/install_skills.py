#!/usr/bin/env python3
"""Mirror .claude/skills/power-* into ~/.claude/skills/ (or another target).

Modes
-----
- ``--mode symlink`` — POSIX symlinks or Windows directory junctions. Falls back
  to copy if symlinks aren't available (e.g. Windows without developer mode).
- ``--mode copy``    — shutil.copytree. Default on Windows.

Each installed skill dir gets a ``.installed-from`` sentinel naming the source
path. ``--uninstall`` only removes dirs carrying that sentinel, so any
hand-authored skills next door are safe.

Exit codes
----------
0 success, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _power_common import EXIT_OK, EXIT_USAGE, die, utc_timestamp  # noqa: E402


SENTINEL = ".installed-from"
SKILL_PREFIX = "power-"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="install_skills.py",
        description="Mirror .claude/skills/power-* into a target skills dir.",
    )
    p.add_argument("--target", default="user",
                   help="'user' (default) ~/.claude/skills, 'project' "
                        "<repo>/.claude/skills, or an explicit PATH")
    p.add_argument("--mode", default=None, choices=("symlink", "copy"),
                   help="Default: symlink on Linux/macOS, copy on Windows")
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


def _default_mode() -> str:
    return "copy" if platform.system() == "Windows" else "symlink"


def _can_symlink(target_dir: Path) -> bool:
    target_dir.mkdir(parents=True, exist_ok=True)
    src = tempfile.mkdtemp(prefix=".symtest-src-", dir=str(target_dir))
    link = Path(target_dir) / (".symtest-link-" + os.urandom(4).hex())
    try:
        os.symlink(src, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False
    finally:
        try:
            if link.exists() or link.is_symlink():
                link.unlink()
        except OSError:
            pass
        try:
            shutil.rmtree(src)
        except OSError:
            pass


def _list_source_skills() -> list[Path]:
    src = _project_root() / ".claude" / "skills"
    if not src.is_dir():
        return []
    return sorted(p for p in src.iterdir()
                  if p.is_dir() and p.name.startswith(SKILL_PREFIX))


def _install_one(src: Path, dst: Path, mode: str, dry_run: bool) -> str:
    if dry_run:
        return f"WOULD {mode}: {src} -> {dst}"
    if dst.exists() or dst.is_symlink():
        # idempotency: same target → no-op
        if mode == "symlink" and dst.is_symlink() and Path(os.readlink(dst)).resolve() == src.resolve():
            return f"noop  : {dst} (already linked)"
        if mode == "copy" and (dst / SENTINEL).is_file():
            try:
                with (dst / SENTINEL).open("r", encoding="utf-8") as f:
                    info = json.load(f)
                if Path(info.get("source", "")).resolve() == src.resolve():
                    # Refresh the copy in case content changed.
                    shutil.rmtree(dst)
                else:
                    return f"skip  : {dst} (different source recorded; remove manually)"
            except (json.JSONDecodeError, OSError):
                return f"skip  : {dst} (sentinel unreadable; remove manually)"
        else:
            return f"skip  : {dst} (exists and not managed by us)"
    if mode == "symlink":
        os.symlink(src, dst, target_is_directory=True)
        # Symlinks don't get a sentinel — they're already self-identifying.
        return f"link  : {dst} -> {src}"
    shutil.copytree(src, dst)
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

    mode = args.mode or _default_mode()
    if mode == "symlink" and not _can_symlink(target):
        print(f"note: symlinks unavailable in {target}; falling back to copy",
              file=sys.stderr)
        mode = "copy"

    for src in sources:
        dst = target / src.name
        print(_install_one(src, dst, mode, args.dry_run))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
