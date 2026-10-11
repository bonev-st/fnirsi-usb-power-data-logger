#!/usr/bin/env python3
"""Pin a run as the baseline for the current branch.

Writes ``runs/.baseline-<branch>.json`` pointing at a chosen run file
(stability.json preferred, CSV fallback). On overwrite, prints the previous
pointer to stderr so nothing is silently lost.

Exit codes
----------
0 success, 2 usage / missing file, 3 if branch detection fails fatally.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _power_common import (  # noqa: E402
    EXIT_OK,
    EXIT_USAGE,
    atomic_write_json,
    die,
    git_branch,
    utc_timestamp,
    write_baseline_pointer_path,
)


_REP_TAIL_RE = re.compile(r"(\.\d+)?(-r\d+)?\.csv$")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="power_baseline.py",
        description="Pin a run as the baseline for the current branch.",
    )
    p.add_argument("run", nargs="?", default=None, type=Path,
                   help="Run CSV or stability.json (default: newest in runs/)")
    p.add_argument("--branch", default=None,
                   help="Override detected branch name")
    p.add_argument("--runs-dir", default=Path("runs"), type=Path)
    p.add_argument("--json", action="store_true",
                   help="Emit machine JSON to stdout")
    return p


def _newest_default(runs_dir: Path) -> Path | None:
    if not runs_dir.is_dir():
        return None
    stab = sorted(runs_dir.glob("*.stability.json"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if stab:
        return stab[0]
    csvs = [p for p in runs_dir.glob("*.csv")
            if not p.name.startswith(".")
            and not p.name.endswith(("-r2.csv", "-r3.csv", "-r4.csv",
                                     "-r5.csv", "-r6.csv", "-r7.csv",
                                     "-r8.csv", "-r9.csv"))]
    if not csvs:
        return None
    return max(csvs, key=lambda p: p.stat().st_mtime)


def _meta_for(run: Path) -> dict[str, Any]:
    """Best-effort load of the sibling .meta.json."""
    base_name = _REP_TAIL_RE.sub("", run.name)
    if run.name.endswith(".stability.json"):
        base_name = run.name[:-len(".stability.json")]
    meta_path = run.parent / f"{base_name}.meta.json"
    if meta_path.is_file():
        try:
            with meta_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    # try sibling -r1 meta
    r1 = run.parent / f"{base_name}-r1.meta.json"
    if r1.is_file():
        try:
            with r1.open("r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    branch = args.branch or git_branch()
    if branch in ("nogit", "detached") and not args.branch:
        die(EXIT_USAGE,
            f"branch is {branch!r}; pass --branch <name> explicitly")

    target = args.run or _newest_default(args.runs_dir)
    if target is None:
        die(EXIT_USAGE,
            f"no run files found in {args.runs_dir}; pass a path or run a measurement first")
    if not target.is_file():
        die(EXIT_USAGE, f"not a file: {target}")

    pointer_path = write_baseline_pointer_path(args.runs_dir, branch)

    previous: dict[str, Any] | None = None
    if pointer_path.is_file():
        try:
            with pointer_path.open("r", encoding="utf-8") as f:
                previous = json.load(f)
        except json.JSONDecodeError:
            previous = None

    meta = _meta_for(target)
    new_pointer = {
        "branch": branch,
        "csv": str(target),
        "sha": meta.get("sha"),
        "dirty": meta.get("dirty"),
        "label": meta.get("label"),
        "set_at_utc": utc_timestamp(),
    }
    atomic_write_json(pointer_path, new_pointer)

    if previous is not None:
        print("previous baseline pointer:", file=sys.stderr)
        json.dump(previous, sys.stderr, separators=(",", ":"))
        sys.stderr.write("\n")

    json.dump(new_pointer, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
