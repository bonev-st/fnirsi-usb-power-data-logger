"""Runtime loop: drive the device, decode frames, and emit CSV rows."""

from __future__ import annotations

import csv
import time
from collections.abc import Iterator
from dataclasses import dataclass
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
from .transport import Transport

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

    def as_dict(self) -> dict[str, float | int]:
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
    ) -> None:
        self._t = transport
        self._verify_crc = verify_crc
        self._on_warn = on_warn or (lambda _msg: None)
        self._refresh_period = refresh_interval_s(transport.info.model)

    def _warn(self, msg: str) -> None:
        self._on_warn(msg)

    def run(self) -> Iterator[tuple[int, int, Sample]]:
        """Yield (packet_index, sample_index_in_packet, sample)."""
        # Startup.
        for frame in startup_sequence(self._t.info.model):
            self._t.write(frame)

        # Drain any stale frames left in the pipe.
        drain_deadline = time.monotonic() + 0.1
        while time.monotonic() < drain_deadline:
            if self._t.read(timeout_ms=50) is None:
                break

        packet_idx = 0
        last_refresh = time.monotonic()
        while True:
            if time.monotonic() - last_refresh >= self._refresh_period:
                try:
                    self._t.write(REFRESH)
                except Exception as e:
                    self._warn(f"refresh write failed: {e}")
                last_refresh = time.monotonic()

            buf = self._t.read(timeout_ms=2000)
            if buf is None:
                self._warn("read timeout; sending refresh")
                try:
                    self._t.write(REFRESH)
                    last_refresh = time.monotonic()
                except Exception as e:
                    self._warn(f"refresh write failed: {e}")
                continue

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


def run_logger(
    transport: Transport,
    sink: RowSink,
    *,
    status: StatusLine | None = None,
    verify_crc: bool = False,
    duration_s: float | None = None,
    alpha: float = 0.9,
    on_warn=None,
) -> int:
    """Drive the transport, emit rows to the sink, return total samples emitted."""
    stream = SampleStream(transport, verify_crc=verify_crc, on_warn=on_warn)

    t0_wall = time.time()
    t0_mono = time.monotonic()
    temp_ema: float | None = None
    energy = 0.0
    capacity = 0.0
    count = 0

    for packet_idx, sample_idx, s in stream.run():
        if temp_ema is None:
            temp_ema = s.temp_c
        else:
            temp_ema = alpha * temp_ema + (1.0 - alpha) * s.temp_c

        energy += s.power * SAMPLE_INTERVAL_S
        capacity += s.current * SAMPLE_INTERVAL_S

        t_elapsed = packet_idx * PACKET_INTERVAL_S + sample_idx * SAMPLE_INTERVAL_S
        row = Row(
            timestamp=t0_wall + t_elapsed,
            t_elapsed=t_elapsed,
            packet=packet_idx,
            sample=sample_idx,
            s=s,
            temp_c_ema=temp_ema,
            energy_ws=energy,
            capacity_as=capacity,
        )
        sink.write(row)
        if status is not None:
            status.update(row)
        count += 1

        if duration_s is not None and (time.monotonic() - t0_mono) >= duration_s:
            break

    return count
