#!/usr/bin/env python3
"""Inspect / validate / probe / init the FNB58 power host config.

Subcommands
-----------
- list           — list all configured hosts (and which is default)
- show <alias>   — dump the resolved host entry
- probe <alias>  — best-effort SSH liveness check
- init [--path]  — write a template host config if none exists

Exit codes
----------
0 OK, 2 usage / config error, 3 SSH/probe failure.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _power_common import (  # noqa: E402
    EXIT_HARDWARE,
    EXIT_OK,
    EXIT_USAGE,
    HOST_ENV,
    die,
    load_host_config,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="power_host.py",
        description="Inspect FNB58 power host config.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="List all configured hosts")
    sp.add_argument("--host-config", default=None, type=Path)
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("show", help="Show one host entry resolved")
    sp.add_argument("alias")
    sp.add_argument("--host-config", default=None, type=Path)
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("probe", help="Best-effort SSH liveness check")
    sp.add_argument("alias")
    sp.add_argument("--host-config", default=None, type=Path)
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("init", help="Write a template host config")
    sp.add_argument("--path", default=None, type=Path,
                    help="Where to write (default: ~/.claude/fnb58-power.json)")
    sp.add_argument("--force", action="store_true",
                    help="Overwrite if exists")
    return p


def _ssh_config_aliases() -> set[str]:
    """Aliases declared in ~/.ssh/config via 'Host <name>' lines."""
    cfg = Path.home() / ".ssh" / "config"
    if not cfg.is_file():
        return set()
    out: set[str] = set()
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.lower().startswith("host "):
            for name in s.split()[1:]:
                if "*" not in name and "?" not in name:
                    out.add(name)
    return out


def _do_list(args: argparse.Namespace) -> int:
    cfg = load_host_config(args.host_config)
    ssh_aliases = _ssh_config_aliases()
    hosts = cfg.get("hosts", {})
    rows: list[dict[str, Any]] = []
    for alias, entry in hosts.items():
        rows.append({
            "alias": alias,
            "ssh_alias": entry.get("ssh_alias"),
            "remote_repo": entry.get("remote_repo"),
            "fnb58_log_cmd": entry.get("fnb58_log_cmd"),
            "is_default": (alias == cfg.get("default_host")),
            "ssh_alias_in_ssh_config": entry.get("ssh_alias") in ssh_aliases,
        })
    payload = {
        "hosts": {r["alias"]: r for r in rows},
        "default_host": cfg.get("default_host"),
        "config_path": cfg.get("_path"),
    }
    if args.json:
        json.dump(payload, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
    else:
        if not rows:
            print("(no hosts configured — local-only mode)")
        for r in rows:
            mark = "*" if r["is_default"] else " "
            warn = "" if r["ssh_alias_in_ssh_config"] else "  [!] not in ~/.ssh/config"
            print(f"{mark} {r['alias']:<20}  ssh={r['ssh_alias']:<20}  "
                  f"cmd={r['fnb58_log_cmd']}{warn}")
    return EXIT_OK


def _do_show(args: argparse.Namespace) -> int:
    cfg = load_host_config(args.host_config)
    if args.alias not in cfg.get("hosts", {}):
        die(EXIT_USAGE,
            f"unknown host alias {args.alias!r}; "
            f"known: {sorted(cfg.get('hosts', {})) or '(none)'}")
    entry = dict(cfg["hosts"][args.alias])
    entry["alias"] = args.alias
    entry["env_override_active"] = bool(os.environ.get(HOST_ENV))
    entry["env_override_value"] = os.environ.get(HOST_ENV)
    if args.json:
        json.dump(entry, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
    else:
        for k, v in entry.items():
            print(f"  {k}: {v}")
    return EXIT_OK


def _do_probe(args: argparse.Namespace) -> int:
    cfg = load_host_config(args.host_config)
    if args.alias not in cfg.get("hosts", {}):
        die(EXIT_USAGE,
            f"unknown host alias {args.alias!r}; "
            f"known: {sorted(cfg.get('hosts', {})) or '(none)'}")
    entry = cfg["hosts"][args.alias]
    if shutil.which("ssh") is None:
        die(EXIT_HARDWARE, "ssh not found in PATH")
    cmd = ["ssh", entry["ssh_alias"],
           "-o", "BatchMode=yes",
           "-o", "ConnectTimeout=5",
           f"test -x {entry['fnb58_log_cmd']} && echo OK"]
    rc = subprocess.run(cmd, capture_output=True, text=True)
    ok = rc.returncode == 0 and "OK" in rc.stdout
    payload = {
        "alias": args.alias,
        "ssh_alias": entry["ssh_alias"],
        "ok": ok,
        "exit_code": rc.returncode,
        "stdout": rc.stdout.strip(),
        "stderr": rc.stderr.strip(),
    }
    if args.json:
        json.dump(payload, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
    else:
        print(f"alias:      {args.alias}")
        print(f"ssh_alias:  {entry['ssh_alias']}")
        print(f"reachable:  {ok}")
        if rc.stdout.strip():
            print(f"stdout:     {rc.stdout.strip()}")
        if rc.stderr.strip():
            print(f"stderr:     {rc.stderr.strip()}")
    return EXIT_OK if ok else EXIT_HARDWARE


def _do_init(args: argparse.Namespace) -> int:
    target = args.path or (Path.home() / ".claude" / "fnb58-power.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not args.force:
        die(EXIT_USAGE, f"refusing to overwrite existing {target} (use --force)")
    here = Path(__file__).resolve().parent.parent
    template = here / "docs" / "examples" / "fnb58-power.example.json"
    if template.is_file():
        shutil.copyfile(template, target)
    else:
        # Inline fallback if the template hasn't been shipped yet.
        target.write_text(json.dumps({
            "default_host": None,
            "hosts": {
                "test-pi": {
                    "ssh_alias": "test-system",
                    "remote_repo": "~/fnb58-logger",
                    "fnb58_log_cmd": "~/fnb58-logger/.venv/bin/fnb58-log",
                    "remote_tmp_dir": "/tmp",
                },
            },
        }, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target}", file=sys.stderr)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return {
        "list": _do_list,
        "show": _do_show,
        "probe": _do_probe,
        "init": _do_init,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
