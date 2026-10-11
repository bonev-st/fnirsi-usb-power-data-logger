#!/usr/bin/env python3
"""Validate a candidate run against a scenario's budgets and a baseline.

Five checks run in fixed order; every failure contributes to a non-zero
exit code:

  1. duration       — candidate.duration_s >= scenario.min_duration_s
  2. required_states — every name in scenario.expected_states present
  3. budget.overall  — every budgets.overall.max_<metric> respected
  4. budget.<STATE>  — every budgets.per_state.<S>.max_<metric> respected
  5. regression      — every regression.max_<metric>_regression_pct respected

Exit codes
----------
0 — every check passed.
1 — one or more checks failed.
2 — usage / scenario / candidate-format error.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _power_common import (  # noqa: E402
    EXIT_OK,
    EXIT_POLICY,
    EXIT_USAGE,
    die,
    git_branch,
    load_candidate_metrics,
    load_scenario,
    write_baseline_pointer_path,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="power_validate.py",
        description="Validate a run against a scenario's budgets and a baseline.",
    )
    p.add_argument("--scenario", required=True, type=Path)
    p.add_argument("--candidate", required=True, type=Path,
                   help="A run CSV or a stability.json")
    p.add_argument("--baseline", default=None, type=Path,
                   help="A run CSV or stability.json; defaults to "
                        "runs/.baseline-<branch>.json")
    p.add_argument("--runs-dir", default=Path("runs"), type=Path,
                   help="Where to look for the baseline pointer (default: runs/)")
    p.add_argument("--json", action="store_true",
                   help="Emit machine JSON to stdout")
    return p


# ---------------------------------------------------------------------------
# Required-states check (CSV path)
# ---------------------------------------------------------------------------


def _states_in_csv(csv_path: Path) -> set[str]:
    states: set[str] = set()
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None or "state" not in reader.fieldnames:
                die(EXIT_USAGE, f"{csv_path}: no 'state' column (is this a fnb58-log CSV?)")
            for row in reader:
                s = (row.get("state") or "").strip()
                if s:
                    states.add(s)
    except FileNotFoundError:
        die(EXIT_USAGE, f"candidate not found: {csv_path}")
    return states


def _states_in_stability(stab_path: Path) -> set[str]:
    """Union the 'states' arrays across each rep's summary.json sibling."""
    with stab_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    states: set[str] = set()
    for rel in data.get("runs", []):
        p = Path(rel)
        if not p.is_file():
            continue
        try:
            with p.open("r", encoding="utf-8") as f:
                summary = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            continue
        for s in summary.get("states", []) or []:
            if s:
                states.add(s)
    return states


# ---------------------------------------------------------------------------
# Baseline resolution
# ---------------------------------------------------------------------------


def _resolve_baseline(
    cli_arg: Path | None,
    runs_dir: Path,
) -> Path | None:
    if cli_arg is not None:
        if not cli_arg.is_file():
            die(EXIT_USAGE, f"baseline not found: {cli_arg}")
        return cli_arg
    pointer = write_baseline_pointer_path(runs_dir, git_branch())
    if not pointer.is_file():
        return None
    try:
        with pointer.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        die(EXIT_USAGE, f"{pointer}: invalid JSON: {e}")
    target = data.get("csv")
    if not target:
        die(EXIT_USAGE, f"{pointer}: missing 'csv' field")
    p = Path(target)
    if not p.is_file():
        die(EXIT_USAGE, f"baseline pointer points at missing file: {p}")
    return p


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _check_duration(scenario: dict[str, Any],
                    candidate: dict[str, Any]) -> dict[str, Any]:
    threshold = float(scenario["min_duration_s"])
    actual = candidate.get("duration_s")
    if actual is None:
        return {"name": "duration", "pass": True,
                "detail": "stability rollup has no duration; skipped"}
    ok = float(actual) >= threshold
    return {
        "name": "duration",
        "pass": ok,
        "detail": f"{float(actual):.3f}s >= {threshold:.3f}s" if ok
                  else f"{float(actual):.3f}s < {threshold:.3f}s",
        "value": float(actual),
        "min": threshold,
    }


def _check_required_states(expected: list[str],
                           seen: set[str]) -> dict[str, Any]:
    missing = sorted(set(expected) - seen)
    return {
        "name": "required_states",
        "pass": not missing,
        "missing": missing,
        "seen": sorted(seen),
        "expected": list(expected),
    }


def _check_budget(scope: str,
                  budget: dict[str, float],
                  metrics: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, limit in budget.items():
        metric = key[len("max_"):]
        actual = metrics.get(metric)
        if actual is None:
            out.append({
                "name": f"budget.{scope}.{key}",
                "pass": False,
                "detail": f"metric {metric!r} unavailable on candidate",
            })
            continue
        ok = float(actual) <= float(limit)
        out.append({
            "name": f"budget.{scope}.{key}",
            "pass": ok,
            "value": float(actual),
            "max": float(limit),
        })
    return out


def _check_regression(regression: dict[str, float],
                      candidate: dict[str, Any],
                      baseline: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not regression:
        return out
    if baseline is None:
        out.append({
            "name": "regression",
            "pass": False,
            "detail": "scenario requires regression check but no baseline available "
                      "(run power_baseline.py first or pass --baseline)",
        })
        return out
    for key, limit_pct in regression.items():
        metric = key[len("max_"):-len("_regression_pct")]
        a = candidate.get(metric)
        b = baseline.get(metric)
        if a is None or b is None:
            out.append({
                "name": f"regression.{metric}",
                "pass": False,
                "detail": f"metric {metric!r} missing on "
                          f"{'candidate' if a is None else 'baseline'}",
            })
            continue
        if float(b) <= 0:
            out.append({
                "name": f"regression.{metric}",
                "pass": True,
                "detail": "baseline <= 0; cannot regress (passed by convention)",
                "value": float(a), "baseline": float(b),
            })
            continue
        delta_pct = (float(a) - float(b)) / float(b) * 100.0
        ok = delta_pct <= float(limit_pct)
        out.append({
            "name": f"regression.{metric}",
            "pass": ok,
            "delta_pct": round(delta_pct, 4),
            "limit_pct": float(limit_pct),
            "value": float(a),
            "baseline": float(b),
        })
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _is_stability(p: Path) -> bool:
    return p.name.endswith(".stability.json")


def _validate(args: argparse.Namespace) -> dict[str, Any]:
    scenario = load_scenario(args.scenario)
    candidate = args.candidate
    if not candidate.is_file():
        die(EXIT_USAGE, f"candidate not found: {candidate}")

    cand_metrics = load_candidate_metrics(candidate, state_filter=None)
    baseline_path = _resolve_baseline(args.baseline, args.runs_dir)
    base_metrics = (load_candidate_metrics(baseline_path, state_filter=None)
                    if baseline_path else None)

    checks: list[dict[str, Any]] = []
    checks.append(_check_duration(scenario, cand_metrics))

    seen_states = (
        _states_in_stability(candidate) if _is_stability(candidate)
        else _states_in_csv(candidate)
    )
    checks.append(_check_required_states(
        scenario["expected_states"], seen_states))

    checks.extend(_check_budget(
        "overall", scenario["budgets"].get("overall", {}), cand_metrics))

    per_state = scenario["budgets"].get("per_state", {})
    if per_state and _is_stability(candidate):
        checks.append({
            "name": "budget.per_state",
            "pass": False,
            "detail": "stability rollup has no per-state slices; "
                      "use a single CSV for per-state budgets",
        })
    else:
        for state, bud in per_state.items():
            sliced = load_candidate_metrics(candidate, state_filter=state)
            checks.extend(_check_budget(state, bud, sliced))

    checks.extend(_check_regression(
        scenario["regression"], cand_metrics, base_metrics))

    failures = [c["name"] + (": " + c.get("detail", "")
                              if c.get("detail") else "")
                for c in checks if not c["pass"]]
    return {
        "scenario": scenario["name"],
        "candidate": str(candidate),
        "baseline": str(baseline_path) if baseline_path else None,
        "checks": checks,
        "pass": all(c["pass"] for c in checks),
        "failures": failures,
    }


def _print_human(report: dict[str, Any]) -> None:
    print(f"scenario:  {report['scenario']}")
    print(f"candidate: {report['candidate']}")
    print(f"baseline:  {report['baseline']}")
    print("-" * 60)
    for c in report["checks"]:
        mark = "PASS" if c["pass"] else "FAIL"
        detail = c.get("detail", "")
        if not detail:
            if "value" in c and "max" in c:
                detail = f"value={c['value']} max={c['max']}"
            elif "delta_pct" in c and "limit_pct" in c:
                detail = f"delta={c['delta_pct']:+.2f}% limit={c['limit_pct']:.2f}%"
            elif "missing" in c:
                detail = (f"missing={c['missing']}" if c["missing"]
                          else f"all expected states present ({c['seen']})")
        print(f"  [{mark}] {c['name']:<48}  {detail}")
    print("-" * 60)
    print("PASS" if report["pass"] else "FAIL")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = _validate(args)
    if args.json:
        json.dump(report, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
    else:
        _print_human(report)
    return EXIT_OK if report["pass"] else EXIT_POLICY


if __name__ == "__main__":
    raise SystemExit(main())
