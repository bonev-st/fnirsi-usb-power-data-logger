#!/usr/bin/env python3
"""Compare two fnb58-log CSV runs and print a human-readable diff.

Usage:
    python scripts/compare.py baseline.csv candidate.csv
    python scripts/compare.py baseline.csv candidate.csv --state DEEP_SLEEP
    python scripts/compare.py baseline.csv candidate.csv --json

Computes the same metrics as `fnb58-log --summary` for each run (mean /
median / p95 current and power, total charge, total energy, duration) and
prints the absolute and percent change from baseline to candidate.

When ``--state NAME`` is given, only rows whose ``state`` column equals NAME
are included (useful for comparing energy-per-state on runs collected with
``--marker-port``).

Exit code is always 0 — this is a reporting tool, not a gate.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path


# Metrics to report, in display order. (key, label, unit, precision)
_METRICS: list[tuple[str, str, str, int]] = [
    ("samples",     "samples",    "",    0),
    ("duration_s",  "duration",   "s",   3),
    ("mean_V",      "mean V",     "V",   4),
    ("mean_mA",     "mean I",     "mA",  3),
    ("median_mA",   "median I",   "mA",  3),
    ("p95_mA",      "p95 I",      "mA",  3),
    ("max_mA",      "max I",      "mA",  3),
    ("mean_mW",     "mean P",     "mW",  3),
    ("median_mW",   "median P",   "mW",  3),
    ("p95_mW",      "p95 P",      "mW",  3),
    ("energy_mWh",  "energy",     "mWh", 4),
    ("charge_mAh",  "charge",     "mAh", 4),
]


@dataclass
class Run:
    path: Path
    state_filter: str | None
    currents_A: list[float]
    powers_W: list[float]
    voltages_V: list[float]
    first_t: float
    last_t: float
    energy_ws_start: float
    energy_ws_end: float
    capacity_as_start: float
    capacity_as_end: float

    @property
    def n(self) -> int:
        return len(self.currents_A)


def _load(path: Path, state_filter: str | None) -> Run:
    currents: list[float] = []
    powers: list[float] = []
    voltages: list[float] = []
    first_t = math.inf
    last_t = -math.inf
    e_start = math.nan
    e_end = 0.0
    q_start = math.nan
    q_end = 0.0

    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"current_A", "voltage_V", "power_W", "t_elapsed",
                    "energy_Ws", "capacity_As"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(
                f"error: {path} missing columns: {sorted(missing)}\n"
                f"  (is this a fnb58-log CSV?)"
            )
        for row in reader:
            if state_filter is not None:
                if row.get("state", "") != state_filter:
                    continue
            i = float(row["current_A"])
            p = float(row["power_W"])
            v = float(row["voltage_V"])
            t = float(row["t_elapsed"])
            e = float(row["energy_Ws"])
            q = float(row["capacity_As"])
            currents.append(i)
            powers.append(p)
            voltages.append(v)
            if t < first_t:
                first_t = t
            if t > last_t:
                last_t = t
            if math.isnan(e_start):
                e_start = e
            if math.isnan(q_start):
                q_start = q
            e_end = e
            q_end = q

    return Run(
        path=path,
        state_filter=state_filter,
        currents_A=currents,
        powers_W=powers,
        voltages_V=voltages,
        first_t=0.0 if first_t is math.inf else first_t,
        last_t=0.0 if last_t == -math.inf else last_t,
        energy_ws_start=0.0 if math.isnan(e_start) else e_start,
        energy_ws_end=e_end,
        capacity_as_start=0.0 if math.isnan(q_start) else q_start,
        capacity_as_end=q_end,
    )


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def summarize(run: Run) -> dict[str, float | int]:
    n = run.n
    if n == 0:
        return {m[0]: 0.0 for m in _METRICS} | {"samples": 0}

    si = sorted(run.currents_A)
    sp = sorted(run.powers_W)
    # Energy / charge: if filtered by state, integrate on the fly from the
    # retained currents at the device's 10 ms cadence (the cumulative columns
    # count *all* samples, not just the filtered subset).
    if run.state_filter is not None:
        dt = 0.010
        energy_ws = sum(p * dt for p in run.powers_W)
        capacity_as = sum(i * dt for i in run.currents_A)
        duration = n * dt
    else:
        energy_ws = run.energy_ws_end - run.energy_ws_start
        capacity_as = run.capacity_as_end - run.capacity_as_start
        duration = max(run.last_t - run.first_t, 0.0)

    mean_i = sum(run.currents_A) / n
    mean_p = sum(run.powers_W) / n
    mean_v = sum(run.voltages_V) / n
    return {
        "samples": n,
        "duration_s": round(duration, 3),
        "mean_V": round(mean_v, 5),
        "mean_mA": round(mean_i * 1000.0, 4),
        "median_mA": round(_percentile(si, 50.0) * 1000.0, 4),
        "p95_mA": round(_percentile(si, 95.0) * 1000.0, 4),
        "max_mA": round(max(run.currents_A) * 1000.0, 4),
        "mean_mW": round(mean_p * 1000.0, 4),
        "median_mW": round(_percentile(sp, 50.0) * 1000.0, 4),
        "p95_mW": round(_percentile(sp, 95.0) * 1000.0, 4),
        "energy_mWh": round(energy_ws / 3.6, 6),
        "charge_mAh": round(capacity_as * 1000.0 / 3600.0, 6),
    }


def _pct(a: float, b: float) -> str:
    """Percent change from a (baseline) to b (candidate)."""
    if a == 0:
        if b == 0:
            return "    0.00%"
        return "      inf%"
    pct = (b - a) / a * 100.0
    sign = "+" if pct >= 0 else ""
    return f"  {sign}{pct:7.2f}%"


def _fmt(v: float | int, prec: int) -> str:
    if isinstance(v, int):
        return f"{v:>12d}"
    return f"{v:>12.{prec}f}"


def print_diff(baseline: Run, candidate: Run, state: str | None) -> None:
    a = summarize(baseline)
    b = summarize(candidate)

    header = f"Comparing  A={baseline.path.name}  B={candidate.path.name}"
    if state is not None:
        header += f"  (state={state})"
    print(header)
    print("-" * len(header))
    print(f"{'metric':<12}  {'A':>12}  {'B':>12}  {'B-A':>12}  {'Δ%':>10}")
    for key, label, unit, prec in _METRICS:
        va = a[key]
        vb = b[key]
        diff = (vb - va) if not isinstance(va, int) else int(vb - va)
        unit_s = f" {unit}" if unit else ""
        diff_s = f"{diff:+.{prec}f}" if not isinstance(va, int) else f"{diff:+d}"
        print(f"{label:<12}  {_fmt(va, prec)}  {_fmt(vb, prec)}  "
              f"{diff_s:>12}{unit_s:<4}  {_pct(float(va), float(vb))}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="compare.py",
        description="Diff two fnb58-log CSV runs.",
    )
    ap.add_argument("baseline", type=Path, help="baseline run CSV")
    ap.add_argument("candidate", type=Path, help="candidate run CSV")
    ap.add_argument(
        "--state", default=None,
        help="only include rows whose 'state' column equals this value",
    )
    ap.add_argument(
        "--json", action="store_true",
        help="emit a machine-readable JSON diff instead of a table",
    )
    args = ap.parse_args(argv)

    baseline = _load(args.baseline, args.state)
    candidate = _load(args.candidate, args.state)

    if args.json:
        a = summarize(baseline)
        b = summarize(candidate)
        diff = {
            k: {
                "a": a[k], "b": b[k],
                "delta": (b[k] - a[k]) if not isinstance(a[k], str) else None,
                "pct": (None if a[k] == 0 else round((b[k] - a[k]) / a[k] * 100.0, 4))
                       if not isinstance(a[k], str) else None,
            }
            for k in a
        }
        out = {
            "baseline": str(baseline.path),
            "candidate": str(candidate.path),
            "state": args.state,
            "a": a, "b": b, "diff": diff,
        }
        json.dump(out, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
    else:
        print_diff(baseline, candidate, args.state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
