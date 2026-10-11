"""End-to-end tests for the power-measurement Skill pipeline.

Hardware-free. Uses ``--replay`` to drive the wrappers with fixture CSVs
so nothing ever touches a real FNB58.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
FIXTURES = REPO / "tests_e2e" / "fixtures"
SCENARIOS = REPO / "scenarios"


def _py(*argv: str, expect_zero: bool = True) -> subprocess.CompletedProcess:
    """Invoke a Python module with our repo on PYTHONPATH."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + str(SCRIPTS)
    # Don't let the host's config leak into tests.
    env.pop("FNB58_POWER_HOST_CONFIG", None)
    env.pop("POWER_HOST", None)
    p = subprocess.run(
        [sys.executable, *argv],
        capture_output=True, text=True, env=env, cwd=REPO,
    )
    if expect_zero and p.returncode != 0:
        raise AssertionError(
            f"{argv!r} exited {p.returncode}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}"
        )
    return p


def _run(tmp_out: Path, label: str, fixture: str = "good_deep_sleep.csv") -> None:
    _py(
        str(SCRIPTS / "power_run.py"),
        "--scenario", str(SCENARIOS / "deep_sleep.json"),
        "--replay", str(FIXTURES / fixture),
        "--out-dir", str(tmp_out),
        "--label", label,
    )


# --------------------------------------------------------------------------
# 1. power_run --replay writes csv/summary/meta/stability + index line
# --------------------------------------------------------------------------


def test_power_run_replay_writes_expected_files(tmp_path: Path):
    out = tmp_path / "runs"
    _run(out, "demo")
    # 3 repetitions per deep_sleep.json → -r1, -r2, -r3 plus .stability.json
    reps = sorted(out.glob("*-demo-r*.csv"))
    assert len(reps) == 3, f"got {reps}"
    for csv_path in reps:
        base = csv_path.with_suffix("")
        assert Path(f"{base}.summary.json").is_file()
        assert Path(f"{base}.meta.json").is_file()
    stab = list(out.glob("*-demo.stability.json"))
    assert len(stab) == 1
    # index.jsonl: one line per repetition
    idx = out / "index.jsonl"
    assert idx.is_file()
    lines = idx.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    for line in lines:
        row = json.loads(line)
        assert row["label"] == "demo"
        assert row["scenario"] == "deep_sleep"
        assert row["host"] is None


# --------------------------------------------------------------------------
# 2. validate good vs good-baseline → PASS
# --------------------------------------------------------------------------


def test_power_validate_passes_good_against_good_baseline(tmp_path: Path):
    out = tmp_path / "runs"
    _run(out, "good")
    stab = next(out.glob("*-good.stability.json"))
    csv_path = next(out.glob("*-good-r1.csv"))
    p = _py(
        str(SCRIPTS / "power_validate.py"),
        "--scenario", str(SCENARIOS / "deep_sleep.json"),
        "--candidate", str(csv_path),
        "--baseline", str(stab),
        "--runs-dir", str(out),
        "--json",
    )
    report = json.loads(p.stdout)
    assert report["pass"] is True, f"expected PASS, got failures: {report['failures']}"
    assert p.returncode == 0
    # Every expected check category is present.
    names = [c["name"] for c in report["checks"]]
    assert "duration" in names
    assert "required_states" in names
    assert any(n.startswith("budget.overall.") for n in names)
    assert any(n.startswith("budget.DEEP_SLEEP.") for n in names)
    assert any(n.startswith("regression.") for n in names)


# --------------------------------------------------------------------------
# 3. validate regressed → FAIL on regression
# --------------------------------------------------------------------------


def test_power_validate_flags_regression(tmp_path: Path):
    out = tmp_path / "runs"
    _run(out, "good")
    stab = next(out.glob("*-good.stability.json"))
    _run(out, "bad", fixture="regressed_deep_sleep.csv")
    csv_path = next(out.glob("*-bad-r1.csv"))
    p = _py(
        str(SCRIPTS / "power_validate.py"),
        "--scenario", str(SCENARIOS / "deep_sleep.json"),
        "--candidate", str(csv_path),
        "--baseline", str(stab),
        "--runs-dir", str(out),
        "--json",
        expect_zero=False,
    )
    assert p.returncode == 1, p.stderr
    report = json.loads(p.stdout)
    assert report["pass"] is False
    # At least one regression failure listed.
    assert any(f.startswith("regression.") for f in report["failures"]), report["failures"]


# --------------------------------------------------------------------------
# 4. validate missing-state → FAIL required_states
# --------------------------------------------------------------------------


def test_power_validate_flags_missing_required_state(tmp_path: Path):
    out = tmp_path / "runs"
    _run(out, "missing", fixture="missing_state.csv")
    csv_path = next(out.glob("*-missing-r1.csv"))
    p = _py(
        str(SCRIPTS / "power_validate.py"),
        "--scenario", str(SCENARIOS / "deep_sleep.json"),
        "--candidate", str(csv_path),
        "--runs-dir", str(out),
        "--json",
        expect_zero=False,
    )
    assert p.returncode == 1
    report = json.loads(p.stdout)
    missing_check = next(c for c in report["checks"] if c["name"] == "required_states")
    assert missing_check["pass"] is False
    assert "DEEP_SLEEP" in missing_check["missing"]


# --------------------------------------------------------------------------
# 5. baseline overwrite prints previous pointer to stderr
# --------------------------------------------------------------------------


def test_power_baseline_overwrites_and_prints_previous(tmp_path: Path):
    out = tmp_path / "runs"
    _run(out, "first")
    _run(out, "second")
    stab1 = next(out.glob("*-first.stability.json"))
    stab2 = next(out.glob("*-second.stability.json"))
    p1 = _py(str(SCRIPTS / "power_baseline.py"),
             str(stab1), "--runs-dir", str(out), "--branch", "testing")
    assert "previous" not in p1.stderr.lower()
    p2 = _py(str(SCRIPTS / "power_baseline.py"),
             str(stab2), "--runs-dir", str(out), "--branch", "testing")
    assert "previous baseline pointer" in p2.stderr.lower()
    # Previous-pointer payload is a JSON object on stderr.
    # Find the JSON line.
    json_lines = [ln for ln in p2.stderr.splitlines() if ln.strip().startswith("{")]
    assert json_lines
    prev = json.loads(json_lines[0])
    assert prev["label"] == "first"


# --------------------------------------------------------------------------
# 6. compare resolves labels by newest mtime
# --------------------------------------------------------------------------


def test_power_compare_resolves_labels_by_newest_mtime(tmp_path: Path):
    out = tmp_path / "runs"
    _run(out, "older")
    _run(out, "newer")
    p = _py(
        str(SCRIPTS / "power_compare.py"),
        "older", "newer",
        "--runs-dir", str(out),
        "--json",
    )
    doc = json.loads(p.stdout)
    assert "older-r1.csv" in doc["baseline"] or "older" in doc["baseline"]
    assert "newer-r1.csv" in doc["candidate"] or "newer" in doc["candidate"]
    # Both fixtures are the 'good' replay, so mean should match.
    assert doc["a"]["mean_mA"] == pytest.approx(doc["b"]["mean_mA"], rel=1e-9)


# --------------------------------------------------------------------------
# 7. runs/index.jsonl is append-only
# --------------------------------------------------------------------------


def test_index_jsonl_is_append_only(tmp_path: Path):
    out = tmp_path / "runs"
    _run(out, "first")
    idx = out / "index.jsonl"
    first_bytes = idx.read_bytes()
    first_lines = idx.read_text(encoding="utf-8").splitlines()
    _run(out, "second")
    new_text = idx.read_text(encoding="utf-8")
    # First lines must be byte-identical (append-only).
    assert new_text.encode("utf-8").startswith(first_bytes)
    assert len(new_text.splitlines()) > len(first_lines)


# --------------------------------------------------------------------------
# 8. host resolution precedence (pure-python, no SSH)
# --------------------------------------------------------------------------


def test_host_resolution_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Load the helper directly rather than via subprocess — it's pure python.
    sys.path.insert(0, str(SCRIPTS))
    try:
        import _power_common as pc
    finally:
        pass

    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({
        "default_host": "D",
        "hosts": {
            "A": {"ssh_alias": "a", "fnb58_log_cmd": "/bin/a"},
            "B": {"ssh_alias": "b", "fnb58_log_cmd": "/bin/b"},
            "C": {"ssh_alias": "c", "fnb58_log_cmd": "/bin/c"},
            "D": {"ssh_alias": "d", "fnb58_log_cmd": "/bin/d"},
        },
    }), encoding="utf-8")
    cfg = pc.load_host_config(cfg_path)

    monkeypatch.delenv(pc.HOST_ENV, raising=False)

    # 1. CLI wins
    assert pc.resolve_host(cfg, cli_arg="A", scenario_default="B")["ssh_alias"] == "a"
    # 2. env wins when CLI absent
    monkeypatch.setenv(pc.HOST_ENV, "B")
    assert pc.resolve_host(cfg, cli_arg=None, scenario_default="C")["ssh_alias"] == "b"
    monkeypatch.delenv(pc.HOST_ENV)
    # 3. scenario default when CLI & env absent
    assert pc.resolve_host(cfg, cli_arg=None, scenario_default="C")["ssh_alias"] == "c"
    # 4. cfg default last
    assert pc.resolve_host(cfg, cli_arg=None, scenario_default=None)["ssh_alias"] == "d"
    # 5. None → local
    empty_cfg = {"hosts": {}, "default_host": None}
    assert pc.resolve_host(empty_cfg, cli_arg=None, scenario_default=None) is None
    # 6. --host local always wins
    assert pc.resolve_host(cfg, cli_arg="local", scenario_default="C") is None


# --------------------------------------------------------------------------
# 9. --dry-run prints the would-be ssh argv
# --------------------------------------------------------------------------


def test_power_run_host_argv_dry_run(tmp_path: Path):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({
        "default_host": None,
        "hosts": {
            "test-pi": {
                "ssh_alias": "test-system",
                "fnb58_log_cmd": "~/fnb58-logger/.venv/bin/fnb58-log",
                "remote_tmp_dir": "/tmp",
            },
        },
    }), encoding="utf-8")
    p = _py(
        str(SCRIPTS / "power_run.py"),
        "--scenario", str(SCENARIOS / "deep_sleep.json"),
        "--host", "test-pi",
        "--host-config", str(cfg_path),
        "--out-dir", str(tmp_path / "runs"),
        "--dry-run",
    )
    doc = json.loads(p.stdout)
    assert doc["dry_run"] is True
    assert doc["host"] == "test-pi"
    assert len(doc["plans"]) == 3  # deep_sleep has repetitions=3
    plan = doc["plans"][0]
    assert plan["mode"] == "remote"
    assert plan["ssh_alias"] == "test-system"
    argv = plan["argv"]
    assert argv[0] == "ssh"
    assert "test-system" in argv
    assert "~/fnb58-logger/.venv/bin/fnb58-log" in argv
    assert "-d" in argv
    assert "--summary" in argv
    assert plan["remote_csv"].startswith("/tmp/fnb58-")


# --------------------------------------------------------------------------
# 9b. local mode runs the logger via this interpreter, not a PATH lookup
# --------------------------------------------------------------------------


def test_power_run_local_argv_uses_sys_executable(tmp_path: Path):
    # Regression: a bare "fnb58-log" argv crashed with FileNotFoundError
    # whenever the venv was not activated (found on real HW, Windows).
    empty = tmp_path / "empty.json"
    empty.write_text('{"hosts": {}, "default_host": null}', encoding="utf-8")
    p = _py(
        str(SCRIPTS / "power_run.py"),
        "--scenario", str(SCENARIOS / "deep_sleep.json"),
        "--host", "local",
        "--host-config", str(empty),
        "--out-dir", str(tmp_path / "runs"),
        "--dry-run",
    )
    plan = json.loads(p.stdout)["plans"][0]
    assert plan["mode"] == "local"
    assert plan["argv"][0] == sys.executable
    assert plan["argv"][1:3] == ["-m", "fnb58_logger"]
    assert "--summary" in plan["argv"]


# --------------------------------------------------------------------------
# 9c. compare human output survives a non-UTF-8 stdout (Windows cp1252)
# --------------------------------------------------------------------------


def test_power_compare_human_output_non_utf8_stdout(tmp_path: Path):
    out = tmp_path / "runs"
    _run(out, "a")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + str(SCRIPTS)
    env["PYTHONIOENCODING"] = "cp1252"
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "power_compare.py"), "a", "a",
         "--runs-dir", str(out)],
        capture_output=True, env=env, cwd=REPO,
    )
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    assert "Δ%" in p.stdout.decode("utf-8")


# --------------------------------------------------------------------------
# 10. install_skills idempotency + uninstall safety
# --------------------------------------------------------------------------


def test_install_skills_idempotent(tmp_path: Path):
    target = tmp_path / "skills"
    # Drop a hand-authored skill sibling — --uninstall must preserve it.
    hand = target / "hand-made"
    hand.mkdir(parents=True)
    (hand / "SKILL.md").write_text("---\nname: hand-made\n---\nhi\n",
                                     encoding="utf-8")

    # First install.
    _py(str(SCRIPTS / "install_skills.py"), "--target", str(target))
    installed_power_measure = target / "power-measure"
    assert installed_power_measure.is_dir()
    sentinel = installed_power_measure / ".installed-from"
    assert sentinel.is_file()

    # Second install refreshes the copy and keeps the sentinel.
    p2 = _py(str(SCRIPTS / "install_skills.py"), "--target", str(target))
    assert installed_power_measure.is_dir()
    assert sentinel.is_file()
    assert "copy" in p2.stdout

    # Uninstall removes managed dirs, preserves hand-made.
    _py(str(SCRIPTS / "install_skills.py"), "--target", str(target),
        "--uninstall")
    assert not installed_power_measure.exists()
    assert hand.is_dir()
    assert (hand / "SKILL.md").is_file()


def test_install_skills_rewrites_script_paths_for_other_repos(tmp_path: Path):
    # Regression: installed skills said "python scripts/power_*.py", which
    # does not exist when Claude Code runs in a firmware repo.
    target = tmp_path / "skills"
    _py(str(SCRIPTS / "install_skills.py"), "--target", str(target))
    text = (target / "power-host" / "SKILL.md").read_text(encoding="utf-8")
    assert "python scripts/" not in text
    cmd_line = next(line.strip() for line in text.splitlines()
                    if "power_host.py" in line and "<subcommand>" in line)
    python, script = cmd_line.split()[:2]
    assert Path(script) == SCRIPTS / "power_host.py"

    # The rewritten command works from an unrelated cwd.
    other = tmp_path / "firmware"
    other.mkdir()
    empty = tmp_path / "empty.json"
    empty.write_text('{"hosts": {}, "default_host": null}', encoding="utf-8")
    p = subprocess.run([python, script, "list", "--json",
                        "--host-config", str(empty)],
                       capture_output=True, text=True, cwd=other)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["hosts"] == {}


# --------------------------------------------------------------------------
# 11. power_host list with no config → empty set, exit 0
# --------------------------------------------------------------------------


def test_power_host_list_with_no_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Point the loader at a file that doesn't exist — but through the env,
    # load_host_config will try and fall back to empty.
    # Easiest: set FNB58_POWER_HOST_CONFIG to something we know is missing
    # and pass --host-config of an empty file? Actually the loader dies if
    # --host-config points at a missing file. So use a truly empty config.
    empty = tmp_path / "empty.json"
    empty.write_text('{"hosts": {}, "default_host": null}', encoding="utf-8")
    p = _py(
        str(SCRIPTS / "power_host.py"), "list",
        "--host-config", str(empty), "--json",
    )
    doc = json.loads(p.stdout)
    assert doc["hosts"] == {}
    assert doc["default_host"] is None
