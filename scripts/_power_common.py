"""Shared helpers for scripts/power_*.py.

Stdlib only. No I/O against the FNB58 device. See
`docs/plans/power-skill.plan.md` §4 for the contract this module
implements.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Exit codes used uniformly by every scripts/power_*.py wrapper.
EXIT_OK = 0
EXIT_POLICY = 1   # validation / policy failure
EXIT_USAGE = 2    # bad scenario, bad args, missing file
EXIT_HARDWARE = 3  # transport / SSH / subprocess failure


def die(code: int, msg: str) -> "None":
    """Print 'error: <msg>' to stderr and SystemExit with the given code."""
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


# ---------------------------------------------------------------------------
# git helpers
# ---------------------------------------------------------------------------


def git_sha(short: int = 10) -> str:
    """<hex>, <hex>-dirty, or 'nogit'."""
    try:
        p = subprocess.run(
            ["git", "rev-parse", f"--short={short}", "HEAD"],
            check=True, capture_output=True, text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "nogit"
    sha = p.stdout.strip()
    try:
        d = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True, capture_output=True, text=True,
        )
        if d.stdout.strip():
            sha += "-dirty"
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return sha


def git_branch() -> str:
    """Branch name, or 'detached', or 'nogit'."""
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            check=True, capture_output=True, text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "nogit"
    name = p.stdout.strip()
    if not name or name == "HEAD":
        return "detached"
    return name


# ---------------------------------------------------------------------------
# Scenario loading + validation
# ---------------------------------------------------------------------------


# Whitelist of metrics that may appear in budgets and regression. Matches
# the numeric subset of SummaryAccumulator.to_dict() in src/fnb58_logger/logger.py.
KNOWN_METRICS: frozenset[str] = frozenset({
    "mean_mA", "median_mA", "p95_mA", "min_mA", "max_mA",
    "mean_mW", "median_mW", "p95_mW",
    "energy_mWh", "charge_mAh",
    "mean_V", "min_V", "max_V",
    "duration_s",
})

_REQUIRED_SCENARIO_FIELDS = ("name", "description", "duration_s",
                             "expected_states", "budgets")


def load_scenario(path: Path) -> dict[str, Any]:
    """Read+validate a scenario JSON. Returns the dict with defaults filled.

    Exits with code 2 on any structural error.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            s = json.load(f)
    except FileNotFoundError:
        die(EXIT_USAGE, f"scenario file not found: {path}")
    except json.JSONDecodeError as e:
        die(EXIT_USAGE, f"{path}: invalid JSON: {e}")

    if not isinstance(s, dict):
        die(EXIT_USAGE, f"{path}: scenario must be a JSON object")

    if s.get("name") != path.stem:
        die(EXIT_USAGE,
            f"{path}: scenario name {s.get('name')!r} must match file stem {path.stem!r}")

    missing = [k for k in _REQUIRED_SCENARIO_FIELDS if k not in s]
    if missing:
        die(EXIT_USAGE, f"{path}: missing required fields: {missing}")

    if not isinstance(s["description"], str):
        die(EXIT_USAGE, f"{path}: description must be a string")
    if not isinstance(s["duration_s"], (int, float)) or s["duration_s"] <= 0:
        die(EXIT_USAGE, f"{path}: duration_s must be a positive number")
    if not isinstance(s["expected_states"], list) or \
            not all(isinstance(x, str) for x in s["expected_states"]):
        die(EXIT_USAGE, f"{path}: expected_states must be a list of strings")

    budgets = s["budgets"]
    if not isinstance(budgets, dict):
        die(EXIT_USAGE, f"{path}: budgets must be an object")
    overall = budgets.get("overall", {})
    if not isinstance(overall, dict):
        die(EXIT_USAGE, f"{path}: budgets.overall must be an object")
    _validate_budget_keys(path, "budgets.overall", overall)
    per_state = budgets.get("per_state", {})
    if not isinstance(per_state, dict):
        die(EXIT_USAGE, f"{path}: budgets.per_state must be an object")
    for state, bud in per_state.items():
        if not isinstance(bud, dict):
            die(EXIT_USAGE, f"{path}: budgets.per_state.{state} must be an object")
        _validate_budget_keys(path, f"budgets.per_state.{state}", bud)
    budgets.setdefault("overall", overall)
    budgets.setdefault("per_state", per_state)

    s.setdefault("repetitions", 1)
    if not isinstance(s["repetitions"], int) or s["repetitions"] < 1:
        die(EXIT_USAGE, f"{path}: repetitions must be a positive int")
    s.setdefault("min_duration_s", float(s["duration_s"]) * 0.9)
    if not isinstance(s["min_duration_s"], (int, float)) or s["min_duration_s"] < 0:
        die(EXIT_USAGE, f"{path}: min_duration_s must be a non-negative number")
    s.setdefault("marker_port", None)
    s.setdefault("marker_baud", 115200)
    s.setdefault("regression", {})
    if not isinstance(s["regression"], dict):
        die(EXIT_USAGE, f"{path}: regression must be an object")
    _validate_regression_keys(path, s["regression"])
    s.setdefault("build_cmd", None)
    s.setdefault("flash_cmd", None)
    s.setdefault("default_host", None)
    return s


def _validate_budget_keys(path: Path, where: str, d: dict[str, Any]) -> None:
    for k, v in d.items():
        if not k.startswith("max_"):
            die(EXIT_USAGE,
                f"{path}: {where}.{k}: budget keys must start with 'max_'")
        metric = k[len("max_"):]
        if metric not in KNOWN_METRICS:
            die(EXIT_USAGE,
                f"{path}: {where}.{k}: unknown metric {metric!r}; "
                f"allowed: {sorted(KNOWN_METRICS)}")
        if not isinstance(v, (int, float)):
            die(EXIT_USAGE, f"{path}: {where}.{k}: value must be a number")


def _validate_regression_keys(path: Path, d: dict[str, Any]) -> None:
    for k, v in d.items():
        if not (k.startswith("max_") and k.endswith("_regression_pct")):
            die(EXIT_USAGE,
                f"{path}: regression.{k}: must be 'max_<metric>_regression_pct'")
        metric = k[len("max_"):-len("_regression_pct")]
        if metric not in KNOWN_METRICS:
            die(EXIT_USAGE,
                f"{path}: regression.{k}: unknown metric {metric!r}; "
                f"allowed: {sorted(KNOWN_METRICS)}")
        if not isinstance(v, (int, float)):
            die(EXIT_USAGE, f"{path}: regression.{k}: value must be a number")


# ---------------------------------------------------------------------------
# Run-path resolution + atomic writes
# ---------------------------------------------------------------------------


def resolve_run_base(
    out_dir: Path,
    sha: str,
    label: str,
    total_repetitions: int = 1,
) -> Path:
    """Pick <out_dir>/<sha>-<label>[.N] such that no future run-file collides."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for n in range(0, 10000):
        suffix = "" if n == 0 else f".{n}"
        base = out_dir / f"{sha}-{label}{suffix}"
        if not _base_collides(base, total_repetitions):
            return base
    raise RuntimeError(
        f"too many collisions in {out_dir} for label {label!r}"
    )


def _base_collides(base: Path, total_repetitions: int) -> bool:
    if total_repetitions <= 1:
        candidates = [
            Path(f"{base}.csv"),
            Path(f"{base}.summary.json"),
            Path(f"{base}.meta.json"),
        ]
    else:
        candidates = [Path(f"{base}.stability.json")]
        for i in range(1, total_repetitions + 1):
            candidates += [
                Path(f"{base}-r{i}.csv"),
                Path(f"{base}-r{i}.summary.json"),
                Path(f"{base}-r{i}.meta.json"),
            ]
    return any(c.exists() for c in candidates)


def paths_for_run(
    base: Path,
    repetition_index: int | None = None,
) -> tuple[Path, Path, Path]:
    """Return (csv, summary_json, meta_json) for a single run under base."""
    stem = Path(f"{base}-r{repetition_index}") if repetition_index else base
    return (
        Path(f"{stem}.csv"),
        Path(f"{stem}.summary.json"),
        Path(f"{stem}.meta.json"),
    )


def stability_path(base: Path) -> Path:
    return Path(f"{base}.stability.json")


def atomic_write_json(path: Path, obj: Any) -> None:
    """Write JSON to <path>.<rand>.tmp then os.replace into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, separators=(",", ":"), sort_keys=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_index_line(index_path: Path, obj: dict[str, Any]) -> None:
    """Append one JSONL line, forcing a literal '\\n' terminator."""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, separators=(",", ":"), sort_keys=False) + "\n"
    with open(index_path, "ab") as f:
        f.write(line.encode("utf-8"))


# ---------------------------------------------------------------------------
# compare.py as a library + candidate metrics + label resolution
# ---------------------------------------------------------------------------


_COMPARE_MODULE = None


def compare_module():
    """Lazily load scripts/compare.py without a scripts/__init__.py."""
    global _COMPARE_MODULE
    if _COMPARE_MODULE is not None:
        return _COMPARE_MODULE
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "fnb58_compare", here / "compare.py",
    )
    if spec is None or spec.loader is None:
        die(EXIT_USAGE, f"could not load compare.py from {here}")
    mod = importlib.util.module_from_spec(spec)
    # Must register before exec_module so @dataclass can resolve cls.__module__.
    sys.modules["fnb58_compare"] = mod
    spec.loader.exec_module(mod)
    _COMPARE_MODULE = mod
    return mod


def load_candidate_metrics(
    path: Path,
    state_filter: str | None = None,
) -> dict[str, Any]:
    """Read metrics from a CSV or stability.json.

    For stability.json: returns the per-metric ``stats[metric].mean`` values.
    Per-state filtering on a stability rollup is rejected (the rollup
    is aggregate-only).
    """
    if not path.is_file():
        die(EXIT_USAGE, f"candidate not found: {path}")
    if path.name.endswith(".stability.json"):
        if state_filter:
            die(EXIT_USAGE,
                f"stability rollup has no per-state slices; "
                f"{path.name} cannot be filtered by state {state_filter!r}")
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        out: dict[str, Any] = {}
        for k, v in data.get("stats", {}).items():
            if isinstance(v, dict) and "mean" in v:
                out[k] = v["mean"]
        out.setdefault("samples", None)
        out.setdefault("duration_s", None)
        out.setdefault("states", data.get("states_seen") or [])
        return out
    # CSV branch — delegate everything to scripts/compare.py
    compare = compare_module()
    run = compare._load(path, state_filter)  # noqa: SLF001 — intentional reuse
    return compare.summarize(run)


_LABEL_PAT_TEMPLATE = r"^[^/\\]+-{label}(\.\d+)?(-r1)?\.csv$"


def resolve_latest(out_dir: Path, label: str) -> Path | None:
    """Newest matching run CSV for <label> under out_dir.

    Matches: <sha>-<label>.csv, <sha>-<label>.N.csv, <sha>-<label>-r1.csv,
    <sha>-<label>.N-r1.csv. Excludes -r2/-r3/... (they aren't the
    representative rep).
    """
    if not out_dir.is_dir():
        return None
    pat = re.compile(_LABEL_PAT_TEMPLATE.format(label=re.escape(label)))
    candidates = [p for p in out_dir.glob("*.csv") if pat.match(p.name)]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# ---------------------------------------------------------------------------
# Host config + resolution (plan §16)
# ---------------------------------------------------------------------------


HOST_ENV = "POWER_HOST"
HOST_CFG_ENV = "FNB58_POWER_HOST_CONFIG"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _host_config_search_paths(explicit: Path | None) -> list[Path]:
    if explicit is not None:
        return [explicit]
    out: list[Path] = []
    env = os.environ.get(HOST_CFG_ENV)
    if env:
        out.append(Path(env))
    out.append(_project_root() / ".claude" / "fnb58-power.local.json")
    out.append(Path.home() / ".claude" / "fnb58-power.json")
    return out


def load_host_config(explicit_path: Path | None = None) -> dict[str, Any]:
    """First found of {env, project, user} wins. Empty dict if nothing found."""
    for p in _host_config_search_paths(explicit_path):
        if p.is_file():
            return _read_host_config(p)
        if explicit_path is not None:
            die(EXIT_USAGE, f"host config not found: {p}")
    return {"hosts": {}, "default_host": None}


def _read_host_config(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        die(EXIT_USAGE, f"{path}: invalid JSON: {e}")
    if not isinstance(raw, dict):
        die(EXIT_USAGE, f"{path}: top-level must be an object")
    hosts = raw.get("hosts", {})
    if not isinstance(hosts, dict):
        die(EXIT_USAGE, f"{path}: 'hosts' must be an object")
    for alias, entry in hosts.items():
        if not isinstance(entry, dict):
            die(EXIT_USAGE, f"{path}: hosts.{alias} must be an object")
        for req in ("ssh_alias", "fnb58_log_cmd"):
            if req not in entry or not isinstance(entry[req], str):
                die(EXIT_USAGE,
                    f"{path}: hosts.{alias} missing required string field {req!r}")
    return {
        "hosts": hosts,
        "default_host": raw.get("default_host"),
        "_path": str(path),
    }


def resolve_host(
    cfg: dict[str, Any],
    cli_arg: str | None,
    scenario_default: str | None,
) -> dict[str, Any] | None:
    """Apply 4-level precedence; return host entry or None for local execution.

    Precedence: --host CLI > $POWER_HOST > scenario.default_host > cfg.default_host.
    Returning None means "execute locally". `--host local` (or env=local) explicitly
    forces local even if a default is configured.
    """
    sources: list[str | None] = [
        cli_arg,
        os.environ.get(HOST_ENV) or None,
        scenario_default,
        cfg.get("default_host"),
    ]
    for cand in sources:
        if cand is None or cand == "":
            continue
        if cand == "local":
            return None
        hosts = cfg.get("hosts", {})
        if cand not in hosts:
            die(EXIT_USAGE,
                f"unknown host alias {cand!r}; "
                f"known aliases: {sorted(hosts) or '(none)'}")
        entry = dict(hosts[cand])
        entry.setdefault("alias", cand)
        entry.setdefault("remote_tmp_dir", "/tmp")
        return entry
    return None


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hostname() -> str:
    return socket.gethostname()


def write_baseline_pointer_path(runs_dir: Path, branch: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", branch)
    return runs_dir / f".baseline-{safe}.json"
