#!/usr/bin/env python3
"""Diff two runs by path or label. Thin wrapper over scripts/compare.py.

If a positional arg is not an existing path, it's treated as a label and
resolved to the newest matching run under runs/. The actual comparison is
delegated to scripts/compare.py via in-process import — never edits or
re-implements that file.

Exit code follows scripts/compare.py: always 0 (reporting tool).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _power_common import (  # noqa: E402
    EXIT_OK,
    EXIT_USAGE,
    compare_module,
    die,
    resolve_latest,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="power_compare.py",
        description="Diff two fnb58-log runs (resolves labels to newest CSV).",
    )
    p.add_argument("baseline", help="Run CSV path or label")
    p.add_argument("candidate", help="Run CSV path or label")
    p.add_argument("--state", default=None, help="Filter rows by state column")
    p.add_argument("--runs-dir", default=Path("runs"), type=Path)
    p.add_argument("--json", action="store_true",
                   help="Emit machine JSON (forwarded to compare.py)")
    return p


def _resolve(arg: str, runs_dir: Path) -> Path:
    p = Path(arg)
    if p.is_file():
        return p
    found = resolve_latest(runs_dir, arg)
    if found is None:
        die(EXIT_USAGE,
            f"could not resolve {arg!r}: not a file and no run with that label "
            f"under {runs_dir}/")
    return found


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    a = _resolve(args.baseline, args.runs_dir)
    b = _resolve(args.candidate, args.runs_dir)

    # compare.py prints "Δ%"; on Windows a piped stdout defaults to cp1252,
    # which cannot encode it (UnicodeEncodeError). Force UTF-8 here rather
    # than touching compare.py.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    compare = compare_module()
    forwarded: list[str] = [str(a), str(b)]
    if args.state:
        forwarded += ["--state", args.state]
    if args.json:
        forwarded += ["--json"]

    rc = compare.main(forwarded)
    return rc if rc is not None else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
