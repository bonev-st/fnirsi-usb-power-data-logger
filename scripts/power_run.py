#!/usr/bin/env python3
"""Record one (or N) labeled fnb58-log run(s) into runs/, with metadata.

Modes
-----
- Local: spawns ``<this python> -m fnb58_logger -o <csv> -d <dur> --summary``
  (same interpreter + this repo's ``src/``, so no activated venv is needed).
- Remote (--host ALIAS): runs the same command on a remote machine over SSH,
  pulls the CSV back with scp, captures the summary JSON over SSH stdout.
- Replay (--replay PATH): reads a pre-recorded CSV and synthesizes the
  summary via the real ``SummaryAccumulator`` — the hardware-free test hook.

Exit codes
----------
0 success, 2 usage / scenario error, 3 hardware / SSH / subprocess error.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Sequence

# Make sibling helpers importable when run as `python scripts/power_run.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _power_common import (  # noqa: E402
    EXIT_HARDWARE,
    EXIT_OK,
    EXIT_USAGE,
    append_index_line,
    atomic_write_json,
    die,
    git_branch,
    git_sha,
    hostname,
    load_host_config,
    load_scenario,
    paths_for_run,
    resolve_host,
    resolve_run_base,
    stability_path,
    utc_timestamp,
)

# Re-use the real summary math — never duplicate it.
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC_DIR))
from fnb58_logger.logger import SummaryAccumulator, Row  # noqa: E402
from fnb58_logger.protocol import Sample  # noqa: E402


# Inter-repetition delay — mitigates the FNB58 wedge on back-to-back opens.
_INTER_REP_SLEEP_S = 1.0


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="power_run.py",
        description="Run a labeled fnb58-log capture (local, remote, or replay).",
    )
    p.add_argument("--scenario", required=True, type=Path,
                   help="Path to scenarios/<name>.json")
    p.add_argument("--label", default=None,
                   help="Label for the run (default: scenario name)")
    p.add_argument("--host", default=None,
                   help="Host alias from fnb58-power.json; 'local' forces local")
    p.add_argument("--host-config", default=None, type=Path,
                   help="Override host-config search path (mainly for tests)")
    p.add_argument("--replay", default=None, type=Path,
                   help="Read this CSV instead of opening the device (test hook)")
    p.add_argument("--marker-port", default=None,
                   help="Override scenario.marker_port")
    p.add_argument("--marker-baud", default=None, type=int,
                   help="Override scenario.marker_baud")
    p.add_argument("--out-dir", "--runs-dir", dest="out_dir",
                   default=Path("runs"), type=Path,
                   help="Where to write CSV/meta/summary/index (default: runs/)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the would-be-executed plan as JSON; do not spawn")
    p.add_argument("--json", action="store_true",
                   help="Always emit machine JSON on stdout (default)")
    return p


# ---------------------------------------------------------------------------
# Scenario → marker resolution
# ---------------------------------------------------------------------------


def _resolve_marker_port(value: Any) -> str | None:
    """`marker_port` may be 'env:NAME' — resolve at run time."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        die(EXIT_USAGE, f"marker_port must be a string or null, got {type(value).__name__}")
    if value.startswith("env:"):
        env_name = value[len("env:"):]
        v = os.environ.get(env_name)
        return v or None
    return value


# ---------------------------------------------------------------------------
# Argv builders
# ---------------------------------------------------------------------------


def _local_argv(
    csv_path: Path,
    duration_s: float,
    marker_port: str | None,
    marker_baud: int,
) -> list[str]:
    # Not the bare ``fnb58-log`` entry point: it is only on PATH inside an
    # activated venv, and would then crash with FileNotFoundError.
    argv = [sys.executable, "-m", "fnb58_logger",
            "-o", str(csv_path), "-d", str(duration_s), "--summary"]
    if marker_port:
        argv += ["--marker-port", marker_port, "--marker-baud", str(marker_baud)]
    return argv


def _remote_argv(
    host: dict[str, Any],
    remote_csv: str,
    duration_s: float,
    marker_port: str | None,
    marker_baud: int,
) -> list[str]:
    remote_cmd_parts = [
        host["fnb58_log_cmd"], "-o", remote_csv,
        "-d", str(duration_s), "--summary",
    ]
    if marker_port:
        remote_cmd_parts += ["--marker-port", marker_port,
                             "--marker-baud", str(marker_baud)]
    return [
        "ssh", host["ssh_alias"],
        "-o", "BatchMode=yes",
        "-o", "ServerAliveInterval=15",
        "--",
        *remote_cmd_parts,
    ]


# ---------------------------------------------------------------------------
# Replay: build summary from a CSV using the real SummaryAccumulator
# ---------------------------------------------------------------------------


def _summary_from_csv(csv_path: Path) -> dict[str, Any]:
    """Replay rows through SummaryAccumulator and return its to_dict()."""
    if not csv_path.is_file():
        die(EXIT_USAGE, f"replay file not found: {csv_path}")
    acc = SummaryAccumulator()
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = {"voltage_V", "current_A", "power_W", "dp_V", "dn_V",
                    "temp_C", "temp_C_ema", "t_elapsed", "energy_Ws",
                    "capacity_As", "packet", "sample", "timestamp"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            die(EXIT_USAGE,
                f"{csv_path}: not a fnb58-log CSV (missing columns: {sorted(missing)})")
        for r in reader:
            sample = Sample(
                voltage=float(r["voltage_V"]),
                current=float(r["current_A"]),
                dp=float(r["dp_V"]),
                dn=float(r["dn_V"]),
                temp_c=float(r["temp_C"]),
                flag=1,
            )
            row = Row(
                timestamp=float(r["timestamp"]),
                t_elapsed=float(r["t_elapsed"]),
                packet=int(r["packet"]),
                sample=int(r["sample"]),
                s=sample,
                temp_c_ema=float(r["temp_C_ema"]),
                energy_ws=float(r["energy_Ws"]),
                capacity_as=float(r["capacity_As"]),
                state=r.get("state", "") or "",
            )
            acc.update(row)
    return acc.to_dict()


# ---------------------------------------------------------------------------
# Run execution
# ---------------------------------------------------------------------------


def _run_one(
    *,
    scenario: dict[str, Any],
    csv_path: Path,
    summary_path: Path,
    meta_path: Path,
    host: dict[str, Any] | None,
    replay: Path | None,
    marker_port: str | None,
    marker_baud: int,
    dry_run: bool,
) -> dict[str, Any]:
    """Execute one repetition. Returns a record describing what happened."""
    duration_s = float(scenario["duration_s"])
    plan: dict[str, Any] = {
        "csv": str(csv_path),
        "summary_json": str(summary_path),
        "meta_json": str(meta_path),
    }
    if replay is not None:
        plan["mode"] = "replay"
        plan["replay_source"] = str(replay)
        if dry_run:
            return plan
        # Actually do the replay: copy CSV into place and synthesize summary.
        shutil.copyfile(replay, csv_path)
        summary = _summary_from_csv(csv_path)
        atomic_write_json(summary_path, summary)
        plan["summary"] = summary
        return plan

    if host is not None:
        run_id = uuid.uuid4().hex[:8]
        remote_csv = f"{host['remote_tmp_dir'].rstrip('/')}/fnb58-{run_id}.csv"
        argv = _remote_argv(host, remote_csv, duration_s, marker_port, marker_baud)
        plan["mode"] = "remote"
        plan["argv"] = argv
        plan["ssh_alias"] = host["ssh_alias"]
        plan["remote_csv"] = remote_csv
        if dry_run:
            return plan
        summary = _spawn_capture_summary(argv, csv_path=None)
        # Pull CSV back, then clean up remote tmp file.
        scp_argv = ["scp", f"{host['ssh_alias']}:{remote_csv}", str(csv_path)]
        rc = subprocess.run(scp_argv, capture_output=True, text=True)
        if rc.returncode != 0:
            die(EXIT_HARDWARE,
                f"scp failed (exit {rc.returncode}): {rc.stderr.strip() or '(no stderr)'}")
        cleanup = subprocess.run(
            ["ssh", host["ssh_alias"], "rm", "-f", remote_csv],
            capture_output=True, text=True,
        )
        if cleanup.returncode != 0:
            print(f"warning: failed to clean up remote tmp {remote_csv}: "
                  f"{cleanup.stderr.strip()}", file=sys.stderr)
        atomic_write_json(summary_path, summary)
        plan["summary"] = summary
        return plan

    # Local hardware path.
    argv = _local_argv(csv_path, duration_s, marker_port, marker_baud)
    plan["mode"] = "local"
    plan["argv"] = argv
    if dry_run:
        return plan
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(_SRC_DIR), env.get("PYTHONPATH")) if p)
    summary = _spawn_capture_summary(argv, csv_path=csv_path, env=env)
    atomic_write_json(summary_path, summary)
    plan["summary"] = summary
    return plan


def _spawn_capture_summary(
    argv: Sequence[str],
    *,
    csv_path: Path | None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Spawn argv; capture stdout as the summary JSON, tee stderr to ours."""
    try:
        proc = subprocess.Popen(
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
    except FileNotFoundError:
        die(EXIT_HARDWARE, f"{argv[0]}: executable not found on PATH")
    out, err = proc.communicate()
    if err:
        sys.stderr.write(err)
        sys.stderr.flush()
    if proc.returncode != 0:
        die(EXIT_HARDWARE,
            f"{argv[0]} exited {proc.returncode}; "
            f"stderr above. argv={argv!r}")
    out = out.strip()
    if not out:
        die(EXIT_HARDWARE,
            f"{argv[0]} produced no summary on stdout (expected --summary JSON)")
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        die(EXIT_HARDWARE,
            f"{argv[0]}: stdout not JSON ({e}); first 200 chars: {out[:200]!r}")
    return {}  # unreachable, keeps type-checker happy


# ---------------------------------------------------------------------------
# Stability rollup
# ---------------------------------------------------------------------------


_ROLLUP_METRICS = ("mean_mA", "p95_mA", "energy_mWh")


def _stability_stats(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in _ROLLUP_METRICS:
        vals = [float(s[m]) for s in summaries if m in s]
        if not vals:
            continue
        entry: dict[str, Any] = {
            "mean": round(mean(vals), 6),
            "min": round(min(vals), 6),
            "max": round(max(vals), 6),
        }
        if len(vals) >= 2:
            sd = stdev(vals)
            entry["stdev"] = round(sd, 6)
            entry["cv"] = round(sd / entry["mean"], 6) if entry["mean"] else 0.0
        else:
            entry["stdev"] = 0.0
            entry["cv"] = 0.0
        out[m] = entry
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    scenario = load_scenario(args.scenario)
    label = args.label or scenario["name"]
    out_dir: Path = args.out_dir
    repetitions = int(scenario["repetitions"])

    # --replay short-circuits host resolution.
    if args.replay is not None:
        host = None
    else:
        cfg = load_host_config(args.host_config)
        host = resolve_host(cfg, args.host, scenario.get("default_host"))

    marker_port = args.marker_port if args.marker_port is not None \
        else _resolve_marker_port(scenario.get("marker_port"))
    marker_baud = args.marker_baud if args.marker_baud is not None \
        else int(scenario.get("marker_baud", 115200))

    sha = git_sha()
    branch = git_branch()
    base = resolve_run_base(out_dir, sha, label, repetitions)

    plans: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    common_meta = {
        "label": label,
        "scenario": scenario["name"],
        "sha": sha.replace("-dirty", ""),
        "dirty": sha.endswith("-dirty"),
        "branch": branch,
        "hostname": hostname(),
        "target_board": os.environ.get("TARGET_BOARD"),
        "fnb58_log_args": ["-d", str(scenario["duration_s"]), "--summary"],
        "total_repetitions": repetitions,
        "replay": str(args.replay) if args.replay else None,
    }

    for rep in (None,) if repetitions == 1 else range(1, repetitions + 1):
        rep_idx = None if rep is None else rep
        csv_path, summary_path, meta_path = paths_for_run(base, rep_idx)
        plan = _run_one(
            scenario=scenario,
            csv_path=csv_path,
            summary_path=summary_path,
            meta_path=meta_path,
            host=host,
            replay=args.replay,
            marker_port=marker_port,
            marker_baud=marker_baud,
            dry_run=args.dry_run,
        )

        meta = dict(common_meta)
        meta["timestamp_utc"] = utc_timestamp()
        meta["repetition_index"] = rep_idx if rep_idx is not None else 1
        if host is not None:
            meta["host"] = {
                "alias": host.get("alias"),
                "ssh_alias": host.get("ssh_alias"),
                "remote_repo": host.get("remote_repo"),
                "remote_csv": plan.get("remote_csv"),
            }
        else:
            meta["host"] = None
        plan["meta"] = meta

        if not args.dry_run:
            atomic_write_json(meta_path, meta)
            append_index_line(out_dir / "index.jsonl", {
                "csv": str(csv_path),
                "summary_json": str(summary_path),
                "meta_json": str(meta_path),
                "sha": meta["sha"],
                "dirty": meta["dirty"],
                "branch": branch,
                "scenario": scenario["name"],
                "label": label,
                "timestamp_utc": meta["timestamp_utc"],
                "repetition_index": meta["repetition_index"],
                "host": (host.get("alias") if host else None),
            })
            summaries.append(plan["summary"])

        plans.append(plan)

        # Inter-rep sleep, but never on the final iteration or in dry-run.
        if (not args.dry_run
                and repetitions > 1
                and rep is not None
                and rep < repetitions):
            time.sleep(_INTER_REP_SLEEP_S)

    # Final emission
    if args.dry_run:
        out = {
            "dry_run": True,
            "label": label,
            "scenario": scenario["name"],
            "sha": sha,
            "host": (host.get("alias") if host else None),
            "base": str(base),
            "repetitions": repetitions,
            "plans": plans,
        }
        json.dump(out, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return EXIT_OK

    if repetitions == 1:
        json.dump(summaries[0], sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return EXIT_OK

    rollup = {
        "label": label,
        "scenario": scenario["name"],
        "sha": sha,
        "branch": branch,
        "repetitions": repetitions,
        "runs": [str(p["summary_json"]) for p in plans],
        "stats": _stability_stats(summaries),
    }
    atomic_write_json(stability_path(base), rollup)
    json.dump(rollup, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
