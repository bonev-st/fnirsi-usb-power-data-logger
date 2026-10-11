#!/usr/bin/env python3
"""Deterministic generator for tests_e2e/fixtures/*.csv.

Regenerating with the same seed produces byte-identical output. Commit
the generator alongside the CSVs so regressions in either are caught.

Three fixtures:
  good_deep_sleep.csv      — passes scenarios/deep_sleep.json
  regressed_deep_sleep.csv — 1.5× current in DEEP_SLEEP → fails regression
  missing_state.csv        — same shape as good, but state column empty
"""

from __future__ import annotations

import csv
import random
from pathlib import Path
from typing import Sequence

# Must match src/fnb58_logger/logger.py:CSV_COLUMNS exactly.
CSV_COLUMNS = [
    "timestamp", "t_elapsed", "packet", "sample",
    "voltage_V", "current_A", "power_W",
    "dp_V", "dn_V", "temp_C", "temp_C_ema",
    "energy_Ws", "capacity_As", "state",
]

DT = 0.010
SAMPLES_PER_PACKET = 4
V = 3.3


def _write(path: Path, rows: Sequence[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _build_rows(
    *,
    sleep_current_A: float,
    state_label: str | None,
    seed: int,
    duration_s: float = 30.0,
    boot_duration_s: float = 0.2,
) -> list[dict[str, object]]:
    """Generate rows mimicking a BOOT then DEEP_SLEEP run.

    sleep_current_A: steady-state current during DEEP_SLEEP (amps).
    state_label: 'DEEP_SLEEP' by default, or None to leave empty.
    """
    rng = random.Random(seed)
    n_total = int(duration_s / DT)
    n_boot = int(boot_duration_s / DT)

    rows: list[dict[str, object]] = []
    energy = 0.0
    capacity = 0.0
    temp_ema = 25.0
    t_base = 1700000000.0

    for i in range(n_total):
        if i < n_boot:
            current = 4.0e-3 + rng.uniform(-1e-4, 1e-4)  # ~4 mA BOOT
            state = "BOOT"
        else:
            current = sleep_current_A + rng.uniform(-5e-6, 5e-6)
            state = state_label if state_label is not None else ""

        power = V * current
        t_elapsed = round(i * DT, 3)
        timestamp = round(t_base + t_elapsed, 3)
        energy += power * DT
        capacity += current * DT
        temp_c = 25.0 + rng.uniform(-0.1, 0.1)
        temp_ema = 0.9 * temp_ema + 0.1 * temp_c
        packet = i // SAMPLES_PER_PACKET
        sample = i % SAMPLES_PER_PACKET
        rows.append({
            "timestamp": timestamp,
            "t_elapsed": t_elapsed,
            "packet": packet,
            "sample": sample,
            "voltage_V": round(V, 5),
            "current_A": round(current, 8),
            "power_W": round(power, 8),
            "dp_V": 0.0,
            "dn_V": 0.0,
            "temp_C": round(temp_c, 1),
            "temp_C_ema": round(temp_ema, 2),
            "energy_Ws": round(energy, 6),
            "capacity_As": round(capacity, 8),
            "state": state,
        })
    return rows


def main() -> None:
    out = Path(__file__).resolve().parent
    # good: DEEP_SLEEP steady at 45 µA → mean 0.0714 mA overall, DEEP-only
    # mean ~45 µA, p95 ~50 µA — well under every budget in deep_sleep.json.
    _write(out / "good_deep_sleep.csv",
           _build_rows(sleep_current_A=45e-6, state_label="DEEP_SLEEP", seed=1))
    # regressed: DEEP_SLEEP steady at 60 µA → DEEP-only p95 still under 80 µA,
    # but overall mean is ~0.086 mA — a 20% regression vs good (>3% limit).
    _write(out / "regressed_deep_sleep.csv",
           _build_rows(sleep_current_A=60e-6, state_label="DEEP_SLEEP", seed=2))
    # missing_state: same current as good, but state column empty throughout.
    # Fails the required_states check (expected_states = ['DEEP_SLEEP']).
    _write(out / "missing_state.csv",
           _build_rows(sleep_current_A=45e-6, state_label=None, seed=3))
    print(f"wrote 3 fixtures to {out}")


if __name__ == "__main__":
    main()
