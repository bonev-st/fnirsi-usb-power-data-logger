"""Runtime loop: drive the device, decode frames, and emit CSV rows."""

from __future__ import annotations

import csv
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import IO, Protocol, TextIO

from .protocol import (
    PACKET_INTERVAL_S,
    SAMPLE_INTERVAL_S,
    BadPacket,
    REFRESH,
    Sample,
    decode_packet,
    refresh_interval_s,
    startup_sequence,
)
from .transport import Transport, TransportError

CSV_COLUMNS = [
    "timestamp",         # seconds since epoch, float
    "t_elapsed",         # seconds since first sample
    "packet",            # packet index
    "sample",            # 0..3 within packet
    "voltage_V",
    "current_A",
    "power_W",
    "dp_V",
    "dn_V",
    "temp_C",            # raw (not EMA)
    "temp_C_ema",
    "energy_Ws",
    "capacity_As",
    "state",             # firmware-reported state (from --marker-port), or ""
]


@dataclass
class Row:
    timestamp: float
    t_elapsed: float
    packet: int
    sample: int
    s: Sample
    temp_c_ema: float
    energy_ws: float
    capacity_as: float
    state: str = ""

    def as_dict(self) -> dict[str, float | int | str]:
        return {
            "timestamp": round(self.timestamp, 3),
            "t_elapsed": round(self.t_elapsed, 3),
            "packet": self.packet,
            "sample": self.sample,
            "voltage_V": round(self.s.voltage, 5),
            "current_A": round(self.s.current, 5),
            "power_W": round(self.s.power, 5),
            "dp_V": round(self.s.dp, 3),
            "dn_V": round(self.s.dn, 3),
            "temp_C": round(self.s.temp_c, 1),
            "temp_C_ema": round(self.temp_c_ema, 2),
            "energy_Ws": round(self.energy_ws, 4),
            "capacity_As": round(self.capacity_as, 5),
            "state": self.state,
        }


class RowSink(Protocol):
    def write(self, row: Row) -> None: ...
    def close(self) -> None: ...


class CsvSink:
    def __init__(self, stream: TextIO, *, close_stream: bool = False) -> None:
        self._stream = stream
        self._close_stream = close_stream
        self._writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS, lineterminator="\n")
        self._writer.writeheader()
        stream.flush()

    def write(self, row: Row) -> None:
        self._writer.writerow(row.as_dict())

    def close(self) -> None:
        try:
            self._stream.flush()
        finally:
            if self._close_stream:
                self._stream.close()


@dataclass
class SummaryAccumulator:
    """Online accumulator for a run's scalar metrics.

    Feeds on ``Row`` objects; produces a JSON-friendly dict via ``to_dict``.
    Stores one float per sample for percentile computation — at 100 Hz over
    an hour that's ~360 000 floats (~10 MB), which is fine for the runs this
    tool is used for. For multi-hour sessions, swap in a streaming estimator.
    """

    _current: list[float] = field(default_factory=list)
    _power: list[float] = field(default_factory=list)
    count: int = 0
    v_sum: float = 0.0
    v_min: float = float("inf")
    v_max: float = float("-inf")
    i_min: float = float("inf")
    i_max: float = float("-inf")
    energy_ws_final: float = 0.0
    capacity_as_final: float = 0.0
    first_t_elapsed: float | None = None
    last_t_elapsed: float = 0.0
    states_seen: set[str] = field(default_factory=set)

    def update(self, row: "Row") -> None:
        self.count += 1
        i = row.s.current
        p = row.s.power
        v = row.s.voltage
        self._current.append(i)
        self._power.append(p)
        self.v_sum += v
        if v < self.v_min:
            self.v_min = v
        if v > self.v_max:
            self.v_max = v
        if i < self.i_min:
            self.i_min = i
        if i > self.i_max:
            self.i_max = i
        self.energy_ws_final = row.energy_ws
        self.capacity_as_final = row.capacity_as
        if self.first_t_elapsed is None:
            self.first_t_elapsed = row.t_elapsed
        self.last_t_elapsed = row.t_elapsed
        if row.state:
            self.states_seen.add(row.state)

    @staticmethod
    def _percentile(sorted_vals: list[float], pct: float) -> float:
        # Linear interpolation between closest ranks. pct in [0, 100].
        if not sorted_vals:
            return 0.0
        if len(sorted_vals) == 1:
            return sorted_vals[0]
        k = (len(sorted_vals) - 1) * (pct / 100.0)
        lo = int(k)
        hi = min(lo + 1, len(sorted_vals) - 1)
        frac = k - lo
        return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac

    def to_dict(self) -> dict[str, float | int | list[str]]:
        n = self.count
        if n == 0:
            return {
                "samples": 0,
                "duration_s": 0.0,
                "mean_V": 0.0, "min_V": 0.0, "max_V": 0.0,
                "mean_mA": 0.0, "median_mA": 0.0, "p95_mA": 0.0,
                "min_mA": 0.0, "max_mA": 0.0,
                "mean_mW": 0.0, "median_mW": 0.0, "p95_mW": 0.0,
                "energy_mWh": 0.0, "charge_mAh": 0.0,
                "states": [],
            }
        duration = max(self.last_t_elapsed - (self.first_t_elapsed or 0.0), 0.0)
        sorted_i = sorted(self._current)
        sorted_p = sorted(self._power)
        mean_i = sum(self._current) / n
        mean_p = sum(self._power) / n
        # Convert Ws -> mWh (/3.6), As -> mAh (/3.6 -> s*A / 3600 * 1000).
        return {
            "samples": n,
            "duration_s": round(duration, 3),
            "mean_V": round(self.v_sum / n, 5),
            "min_V": round(self.v_min, 5),
            "max_V": round(self.v_max, 5),
            "mean_mA": round(mean_i * 1000.0, 4),
            "median_mA": round(self._percentile(sorted_i, 50.0) * 1000.0, 4),
            "p95_mA": round(self._percentile(sorted_i, 95.0) * 1000.0, 4),
            "min_mA": round(self.i_min * 1000.0, 4),
            "max_mA": round(self.i_max * 1000.0, 4),
            "mean_mW": round(mean_p * 1000.0, 4),
            "median_mW": round(self._percentile(sorted_p, 50.0) * 1000.0, 4),
            "p95_mW": round(self._percentile(sorted_p, 95.0) * 1000.0, 4),
            "energy_mWh": round(self.energy_ws_final / 3.6, 6),
            "charge_mAh": round(self.capacity_as_final * 1000.0 / 3600.0, 6),
            "states": sorted(self.states_seen),
        }


class StatusLine:
    """~10 Hz status line written to a tty stream (usually stderr)."""

    def __init__(self, stream: IO[str], *, interval_s: float = 0.1) -> None:
        self._stream = stream
        self._interval = interval_s
        self._next_print = 0.0
        self._enabled = bool(stream) and stream.isatty()

    def update(self, row: Row) -> None:
        if not self._enabled:
            return
        now = time.monotonic()
        if now < self._next_print:
            return
        self._next_print = now + self._interval
        msg = (
            f"\r{row.t_elapsed:8.2f}s  "
            f"V={row.s.voltage:7.4f}  "
            f"I={row.s.current:7.4f}  "
            f"P={row.s.power:7.3f}W  "
            f"E={row.energy_ws:9.2f}Ws  "
            f"Q={row.capacity_as:8.4f}As  "
            f"T={row.temp_c_ema:5.1f}°C  "
            f"D+={row.s.dp:4.2f} D-={row.s.dn:4.2f}  "
        )
        self._stream.write(msg)
        self._stream.flush()

    def finalize(self) -> None:
        if self._enabled:
            self._stream.write("\n")
            self._stream.flush()


class SampleStream:
    """Iterate decoded samples from an open Transport.

    Handles startup, periodic refresh, and packet-level errors. Raises on
    fatal transport failures; otherwise logs soft errors via the on_warn
    callback (if given) and keeps going.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        verify_crc: bool = False,
        on_warn=None,
        max_consecutive_timeouts: int = 3,
    ) -> None:
        self._t = transport
        self._verify_crc = verify_crc
        self._on_warn = on_warn or (lambda _msg: None)
        self._refresh_period = refresh_interval_s(transport.info.model)
        self._max_consecutive_timeouts = max_consecutive_timeouts

    def _warn(self, msg: str) -> None:
        self._on_warn(msg)

    def run(self) -> Iterator[tuple[int, int, Sample]]:
        """Yield (packet_index, sample_index_in_packet, sample)."""
        # Startup — match the reference loggers (baryluk, didim99): write the
        # three probe frames, then sleep 100 ms to let the device's firmware
        # enter its streaming state before we start reading.
        for frame in startup_sequence(self._t.info.model):
            self._t.write(frame)
        time.sleep(0.1)

        packet_idx = 0
        last_refresh = time.monotonic()
        consecutive_timeouts = 0
        while True:
            if time.monotonic() - last_refresh >= self._refresh_period:
                try:
                    self._t.write(REFRESH)
                except Exception as e:
                    self._warn(f"refresh write failed: {e}")
                last_refresh = time.monotonic()

            # 5 s matches the reference. Do NOT send an extra REFRESH on
            # timeout — the periodic cadence above handles that. Piling on
            # unpaced OUT frames is what used to wedge the device's USB MCU
            # (symptom: device needs a physical replug).
            buf = self._t.read(timeout_ms=5000)
            if buf is None:
                consecutive_timeouts += 1
                self._warn(
                    f"read timeout ({consecutive_timeouts}/"
                    f"{self._max_consecutive_timeouts})"
                )
                if consecutive_timeouts >= self._max_consecutive_timeouts:
                    raise TransportError(
                        "device stopped streaming — try unplugging and replugging"
                    )
                continue
            consecutive_timeouts = 0

            try:
                samples = decode_packet(buf, verify_crc=self._verify_crc)
            except BadPacket as e:
                self._warn(f"bad packet: {e}")
                continue
            if samples is None:
                continue  # non-data frame, keep going

            for i, s in enumerate(samples):
                yield packet_idx, i, s
            packet_idx += 1

    def drain_fifo(self, max_ms: int = 2000) -> int:
        """Empty the device's IN FIFO before we close.

        Skipping this is what the didim99 repo's README documents as causing
        the meter's display to freeze after exit — the device keeps trying
        to push frames it has queued, and its USB MCU eventually wedges when
        they have nowhere to go. Returns the number of frames drained.
        """
        deadline = time.monotonic() + max_ms / 1000.0
        drained = 0
        while time.monotonic() < deadline:
            try:
                buf = self._t.read(timeout_ms=1000)
            except Exception:
                break  # transport is already dead; nothing we can do.
            if buf is None:
                break
            drained += 1
        return drained


def run_logger(
    transport: Transport,
    sink: RowSink,
    *,
    status: StatusLine | None = None,
    verify_crc: bool = False,
    duration_s: float | None = None,
    alpha: float = 0.9,
    on_warn=None,
    summary: SummaryAccumulator | None = None,
    state_provider: Callable[[], str] | None = None,
    drain_max_ms: int = 2000,
    max_consecutive_timeouts: int = 3,
) -> int:
    """Drive the transport, emit rows to the sink, return total samples emitted.

    Optional hooks:
    - ``summary``: if given, ``.update(row)`` is called for every row so the
      caller can serialise metrics after the run.
    - ``state_provider``: callable returning a short state label (e.g. from an
      external UART reader); written to the ``state`` CSV column.
    - ``drain_max_ms``: upper bound on the post-run IN FIFO drain. Default 2 s.
    """
    stream = SampleStream(
        transport,
        verify_crc=verify_crc,
        on_warn=on_warn,
        max_consecutive_timeouts=max_consecutive_timeouts,
    )

    t0_wall = time.time()
    t0_mono = time.monotonic()
    temp_ema: float | None = None
    energy = 0.0
    capacity = 0.0
    count = 0

    try:
        for packet_idx, sample_idx, s in stream.run():
            if temp_ema is None:
                temp_ema = s.temp_c
            else:
                temp_ema = alpha * temp_ema + (1.0 - alpha) * s.temp_c

            energy += s.power * SAMPLE_INTERVAL_S
            capacity += s.current * SAMPLE_INTERVAL_S

            t_elapsed = packet_idx * PACKET_INTERVAL_S + sample_idx * SAMPLE_INTERVAL_S
            state = state_provider() if state_provider is not None else ""
            row = Row(
                timestamp=t0_wall + t_elapsed,
                t_elapsed=t_elapsed,
                packet=packet_idx,
                sample=sample_idx,
                s=s,
                temp_c_ema=temp_ema,
                energy_ws=energy,
                capacity_as=capacity,
                state=state,
            )
            sink.write(row)
            if status is not None:
                status.update(row)
            if summary is not None:
                summary.update(row)
            count += 1

            if duration_s is not None and (time.monotonic() - t0_mono) >= duration_s:
                break
    finally:
        # Always drain the device's IN FIFO before the transport is closed,
        # even on Ctrl-C, duration expiry, or exception. Without this the
        # FNB58 frequently wedges and needs a physical replug.
        try:
            drained = stream.drain_fifo(max_ms=drain_max_ms)
            if drained > 0 and on_warn is not None:
                on_warn(f"drained {drained} trailing frame(s) on exit")
        except Exception as e:
            if on_warn is not None:
                on_warn(f"drain-on-exit failed: {e}")

    return count
