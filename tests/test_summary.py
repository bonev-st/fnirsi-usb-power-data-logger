"""Tests for SummaryAccumulator, the state column, and scripts/compare.py."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from fnb58_logger.logger import (
    CSV_COLUMNS,
    CsvSink,
    Row,
    SummaryAccumulator,
    run_logger,
)
from fnb58_logger.protocol import MAGIC, PACKET_TYPE_DATA, Model, Sample, crc8_fnirsi
import struct


# --- SummaryAccumulator ------------------------------------------------------

def _row(v: float, i: float, t_elapsed: float, energy: float, capacity: float,
         state: str = "") -> Row:
    return Row(
        timestamp=0.0,
        t_elapsed=t_elapsed,
        packet=0,
        sample=0,
        s=Sample(voltage=v, current=i, dp=0.0, dn=0.0, temp_c=25.0, flag=1),
        temp_c_ema=25.0,
        energy_ws=energy,
        capacity_as=capacity,
        state=state,
    )


def test_summary_empty_run():
    s = SummaryAccumulator()
    d = s.to_dict()
    assert d["samples"] == 0
    assert d["mean_mA"] == 0.0
    assert d["energy_mWh"] == 0.0
    assert d["states"] == []


def test_summary_single_sample():
    s = SummaryAccumulator()
    s.update(_row(v=5.0, i=0.1, t_elapsed=0.0, energy=0.005, capacity=0.001))
    d = s.to_dict()
    assert d["samples"] == 1
    assert d["mean_V"] == pytest.approx(5.0)
    assert d["mean_mA"] == pytest.approx(100.0)
    assert d["median_mA"] == pytest.approx(100.0)
    assert d["p95_mA"] == pytest.approx(100.0)
    # 0.005 Ws / 3.6 -> mWh
    assert d["energy_mWh"] == pytest.approx(0.005 / 3.6, abs=1e-6)
    # 0.001 As * 1000 / 3600 -> mAh
    assert d["charge_mAh"] == pytest.approx(0.001 * 1000.0 / 3600.0, abs=1e-6)


def test_summary_metrics_across_varied_samples():
    s = SummaryAccumulator()
    # 100 samples, current sweeping 0..99 mA, 10 ms each.
    for n in range(100):
        i_A = n / 1000.0   # 0..99 mA
        v = 5.0
        # cumulative energy_Ws = sum(v*i*dt) with dt=0.01
        # we don't actually rely on this being the real integral;
        # SummaryAccumulator just takes the final value of energy_ws.
        s.update(_row(
            v=v, i=i_A, t_elapsed=n * 0.01,
            energy=v * sum(k / 1000.0 for k in range(n + 1)) * 0.01,
            capacity=sum(k / 1000.0 for k in range(n + 1)) * 0.01,
            state="ACTIVE" if n % 2 else "IDLE",
        ))
    d = s.to_dict()
    assert d["samples"] == 100
    # mean of 0..99 mA = 49.5 mA
    assert d["mean_mA"] == pytest.approx(49.5, abs=0.01)
    # median is 49.5 (interpolation between index 49 and 50 = 49, 50)
    assert d["median_mA"] == pytest.approx(49.5, abs=0.01)
    # p95: 95th percentile of 0..99 mA via linear interp
    assert d["p95_mA"] == pytest.approx(94.05, abs=0.1)
    assert d["max_mA"] == pytest.approx(99.0)
    assert d["min_mA"] == pytest.approx(0.0)
    assert sorted(d["states"]) == ["ACTIVE", "IDLE"]
    # duration = last_t - first_t = 0.99
    assert d["duration_s"] == pytest.approx(0.99, abs=1e-6)


def test_summary_updates_states_only_when_nonempty():
    s = SummaryAccumulator()
    s.update(_row(5.0, 0.1, 0.0, 0.005, 0.001, state=""))
    s.update(_row(5.0, 0.1, 0.01, 0.01, 0.002, state="ACTIVE"))
    assert s.to_dict()["states"] == ["ACTIVE"]


# --- state column ------------------------------------------------------------

def test_csv_columns_include_state_at_end():
    assert CSV_COLUMNS[-1] == "state"


def test_row_as_dict_has_state_key():
    row = _row(5.0, 0.1, 0.0, 0.0, 0.0, state="DEEP_SLEEP")
    d = row.as_dict()
    assert d["state"] == "DEEP_SLEEP"
    assert set(d.keys()) == set(CSV_COLUMNS)


def _build_data_frame(samples: list[tuple[int, int, int, int, int, int]]) -> bytes:
    payload = b"".join(struct.pack("<IIHHBH", *s) for s in samples)
    body = bytes([PACKET_TYPE_DATA]) + payload + b"\x00"
    crc = crc8_fnirsi(body)
    return bytes([MAGIC]) + body + bytes([crc])


class _FakeTransport:
    class _Info:
        model = Model.FNB58
    info = _Info()

    def __init__(self, frames: list[bytes]) -> None:
        self._frames = list(frames)
        self._i = 0

    def write(self, frame: bytes) -> None:
        pass

    def read(self, timeout_ms: int = 1000) -> bytes | None:
        frame = self._frames[self._i % len(self._frames)]
        self._i += 1
        return frame

    def close(self) -> None:
        pass


def test_run_logger_writes_state_column_via_state_provider():
    frames = [_build_data_frame([
        (500000, 100000, 5000, 50, 1, 250),
        (500000, 100000, 5000, 50, 1, 250),
        (500000, 100000, 5000, 50, 1, 250),
        (500000, 100000, 5000, 50, 1, 250),
    ])]
    t = _FakeTransport(frames)
    buf = io.StringIO()
    sink = CsvSink(buf)
    summary = SummaryAccumulator()
    state = {"v": "BOOT"}
    run_logger(
        t, sink, duration_s=0.25,
        summary=summary,
        state_provider=lambda: state["v"],
        drain_max_ms=50,
    )
    sink.close()

    header, *rows = buf.getvalue().strip().splitlines()
    assert header.split(",")[-1] == "state"
    # every data row should carry "BOOT"
    for r in rows:
        assert r.split(",")[-1] == "BOOT"
    assert summary.to_dict()["states"] == ["BOOT"]


# --- scripts/compare.py ------------------------------------------------------

_SCRIPT = Path(__file__).parent.parent / "scripts" / "compare.py"


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    import csv as _csv
    with path.open("w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=CSV_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _mk_row(t: float, v: float, i: float, p: float, e_ws: float, q_as: float,
            state: str = "") -> dict[str, object]:
    return {
        "timestamp": 1700000000.0 + t,
        "t_elapsed": round(t, 3),
        "packet": int(t * 100) // 4,
        "sample": int(t * 100) % 4,
        "voltage_V": v,
        "current_A": i,
        "power_W": p,
        "dp_V": 0.0,
        "dn_V": 0.0,
        "temp_C": 25.0,
        "temp_C_ema": 25.0,
        "energy_Ws": e_ws,
        "capacity_As": q_as,
        "state": state,
    }


def test_compare_reports_diff_json(tmp_path: Path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    # 10 samples at 5 V, A draws 100 mA, B draws 50 mA.
    _write_csv(a, [
        _mk_row(n * 0.01, 5.0, 0.1, 0.5, 0.5 * (n + 1) * 0.01, 0.1 * (n + 1) * 0.01)
        for n in range(10)
    ])
    _write_csv(b, [
        _mk_row(n * 0.01, 5.0, 0.05, 0.25, 0.25 * (n + 1) * 0.01, 0.05 * (n + 1) * 0.01)
        for n in range(10)
    ])

    out = subprocess.run(
        [sys.executable, str(_SCRIPT), str(a), str(b), "--json"],
        check=True, capture_output=True, text=True,
    )
    report = json.loads(out.stdout)
    # Candidate mean mA is half of baseline.
    assert report["a"]["mean_mA"] == pytest.approx(100.0)
    assert report["b"]["mean_mA"] == pytest.approx(50.0)
    assert report["diff"]["mean_mA"]["pct"] == pytest.approx(-50.0, abs=0.01)


def test_compare_state_filter(tmp_path: Path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    # A: ACTIVE rows 100 mA, IDLE rows 10 mA.
    _write_csv(a, [
        _mk_row(0.00, 5.0, 0.1, 0.5, 0.005, 0.001, state="ACTIVE"),
        _mk_row(0.01, 5.0, 0.1, 0.5, 0.010, 0.002, state="ACTIVE"),
        _mk_row(0.02, 5.0, 0.01, 0.05, 0.0105, 0.0021, state="IDLE"),
    ])
    # B: ACTIVE rows 50 mA, IDLE rows 10 mA (same idle).
    _write_csv(b, [
        _mk_row(0.00, 5.0, 0.05, 0.25, 0.0025, 0.0005, state="ACTIVE"),
        _mk_row(0.01, 5.0, 0.05, 0.25, 0.005, 0.001, state="ACTIVE"),
        _mk_row(0.02, 5.0, 0.01, 0.05, 0.0055, 0.0011, state="IDLE"),
    ])

    out = subprocess.run(
        [sys.executable, str(_SCRIPT), str(a), str(b), "--state", "ACTIVE", "--json"],
        check=True, capture_output=True, text=True,
    )
    report = json.loads(out.stdout)
    assert report["a"]["samples"] == 2
    assert report["b"]["samples"] == 2
    assert report["a"]["mean_mA"] == pytest.approx(100.0)
    assert report["b"]["mean_mA"] == pytest.approx(50.0)

    # And with --state IDLE, both sides should be equal (no delta).
    out_idle = subprocess.run(
        [sys.executable, str(_SCRIPT), str(a), str(b), "--state", "IDLE", "--json"],
        check=True, capture_output=True, text=True,
    )
    rep_idle = json.loads(out_idle.stdout)
    assert rep_idle["diff"]["mean_mA"]["pct"] == pytest.approx(0.0, abs=0.01)


def test_compare_rejects_non_fnb58_csv(tmp_path: Path):
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n")
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), str(bad), str(bad)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "missing columns" in result.stderr
