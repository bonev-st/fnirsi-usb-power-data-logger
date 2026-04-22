"""Hardware-free tests for the FNIRSI protocol decoder."""

from __future__ import annotations

import io
import struct

import pytest

from fnb58_logger.logger import CSV_COLUMNS, CsvSink, Row, run_logger
from fnb58_logger.protocol import (
    MAGIC,
    PACKET_SIZE,
    PACKET_TYPE_DATA,
    PROBE_1,
    PROBE_2,
    REFRESH,
    BadPacket,
    Model,
    Sample,
    crc8_fnirsi,
    decode_packet,
    refresh_interval_s,
    startup_sequence,
)


# --- CRC ---------------------------------------------------------------------

def test_crc_known_startup_frames():
    # Byte 0 is magic (0xAA); CRC is computed over bytes 1..62 of the 64-byte
    # frame and stored in byte 63. Hand-verified against the reference repo.
    assert PROBE_1[0] == MAGIC and PROBE_1[1] == 0x81 and PROBE_1[-1] == 0x8E
    assert PROBE_2[0] == MAGIC and PROBE_2[1] == 0x82 and PROBE_2[-1] == 0x96
    assert REFRESH[0] == MAGIC and REFRESH[1] == 0x83 and REFRESH[-1] == 0x9E


def test_crc_roundtrip():
    body = bytes([0x04]) + bytes(range(61))
    crc = crc8_fnirsi(body)
    assert 0 <= crc <= 0xFF
    # Recomputing gives the same value.
    assert crc8_fnirsi(body) == crc


# --- Frame builders ----------------------------------------------------------

def test_startup_sequence_fnb58_has_two_probe2():
    seq = startup_sequence(Model.FNB58)
    assert seq == [PROBE_1, PROBE_2, PROBE_2]


def test_startup_sequence_fnb48_ends_with_refresh():
    seq = startup_sequence(Model.FNB48)
    assert seq == [PROBE_1, PROBE_2, REFRESH]


def test_refresh_interval_is_3ms_for_all_models():
    # All four models now use 3 ms. Newer FNB58 firmware (≥V0.68)
    # wedges on the old 1 s cadence. See issue #19 upstream.
    for m in (Model.FNB48, Model.FNB48S, Model.FNB58, Model.C1):
        assert refresh_interval_s(m) == pytest.approx(0.003)


# --- decode_packet -----------------------------------------------------------

def _build_sample(v: int, i: int, dp: int, dn: int, flag: int, t: int) -> bytes:
    return struct.pack("<IIHHBH", v, i, dp, dn, flag, t)


def _build_data_frame(samples: list[tuple[int, int, int, int, int, int]]) -> bytes:
    assert len(samples) == 4
    payload = b"".join(_build_sample(*s) for s in samples)
    # Header byte1 = 0x04 (data), then 60 bytes payload, then byte 62 (constant), then CRC.
    body = bytes([PACKET_TYPE_DATA]) + payload + b"\x00"  # 62 bytes total
    crc = crc8_fnirsi(body)
    return bytes([MAGIC]) + body + bytes([crc])


def test_decode_data_frame_fields():
    frame = _build_data_frame([
        (500000, 100000, 5000, 50, 1, 250),    # 5.0 V, 1.0 A, D+=5.0, D-=0.05, 25.0 C
        (500001, 100001, 5001, 51, 1, 251),
        (500002, 100002, 5002, 52, 1, 252),
        (500003, 100003, 5003, 53, 1, 253),
    ])
    samples = decode_packet(frame, verify_crc=True)
    assert samples is not None and len(samples) == 4
    assert samples[0] == Sample(
        voltage=pytest.approx(5.0),
        current=pytest.approx(1.0),
        dp=pytest.approx(5.0),
        dn=pytest.approx(0.05),
        temp_c=pytest.approx(25.0),
        flag=1,
    )
    assert samples[0].power == pytest.approx(5.0)


def test_decode_non_data_frame_returns_none():
    body = bytes([0x03]) + b"\x00" * 61
    frame = bytes([MAGIC]) + body + bytes([crc8_fnirsi(body)])
    assert decode_packet(frame) is None


def test_decode_rejects_wrong_length():
    with pytest.raises(BadPacket):
        decode_packet(b"\xAA\x04" + b"\x00" * 40)


def test_decode_rejects_bad_magic():
    frame = bytes([0x55]) + bytes([0x04]) + b"\x00" * 61 + b"\x00"
    with pytest.raises(BadPacket):
        decode_packet(frame)


def test_decode_rejects_bad_crc_when_verified():
    frame = bytearray(_build_data_frame([(0, 0, 0, 0, 1, 0)] * 4))
    frame[-1] ^= 0xFF
    with pytest.raises(BadPacket):
        decode_packet(bytes(frame), verify_crc=True)
    # Without verify_crc, it still decodes.
    out = decode_packet(bytes(frame), verify_crc=False)
    assert out is not None and len(out) == 4


def test_frame_length_invariants():
    assert len(PROBE_1) == len(PROBE_2) == len(REFRESH) == PACKET_SIZE


# --- Logger integration (fake transport) -------------------------------------

class _FakeTransport:
    """Minimal Transport for testing run_logger without hardware.

    Cycles through the supplied frames indefinitely on read(); run_logger
    should terminate via duration_s, not by running out of frames.
    """

    class _Info:
        model = Model.FNB58

    info = _Info()

    def __init__(self, frames: list[bytes]) -> None:
        self._frames = list(frames)
        self._i = 0
        self.writes: list[bytes] = []

    def write(self, frame: bytes) -> None:
        self.writes.append(frame)

    def read(self, timeout_ms: int = 1000) -> bytes | None:
        if not self._frames:
            return None
        frame = self._frames[self._i % len(self._frames)]
        self._i += 1
        return frame

    def close(self) -> None:
        pass


def test_run_logger_emits_csv_rows():
    frames = [
        _build_data_frame([
            (500000, 100000, 5000, 50, 1, 250),
            (500000, 100000, 5000, 50, 1, 250),
            (500000, 100000, 5000, 50, 1, 250),
            (500000, 100000, 5000, 50, 1, 250),
        ]),
    ]
    t = _FakeTransport(frames)
    buf = io.StringIO()
    sink = CsvSink(buf)
    # 0.3s is generous for the 100ms startup settle + some samples.
    # drain_max_ms=50 keeps the test fast even though the fake would otherwise
    # feed frames forever.
    count = run_logger(t, sink, duration_s=0.3, verify_crc=True, drain_max_ms=50)
    sink.close()

    # Startup sequence was sent.
    assert t.writes[:3] == [PROBE_1, PROBE_2, PROBE_2]

    # At least the 4 samples from that one packet.
    assert count >= 4
    csv_text = buf.getvalue()
    assert csv_text.splitlines()[0] == ",".join(CSV_COLUMNS)
    # Energy accumulates across samples: with V=5, I=1, dt=0.01s → +0.05 Ws/sample.
    last_line = csv_text.strip().splitlines()[-1]
    cells = last_line.split(",")
    energy_ws = float(cells[CSV_COLUMNS.index("energy_Ws")])
    assert energy_ws > 0


class _TimeoutTransport:
    """Fake transport that returns None for every read (simulating a hung device).

    Records every write so tests can count them.
    """

    class _Info:
        model = Model.FNB58

    info = _Info()

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.reads_called = 0

    def write(self, frame: bytes) -> None:
        self.writes.append(frame)

    def read(self, timeout_ms: int = 1000) -> bytes | None:
        self.reads_called += 1
        return None

    def close(self) -> None:
        pass


def test_read_timeout_does_not_spam_refresh():
    """Regression: old code issued a REFRESH on every read timeout, which piled
    up OUT frames and wedged the device firmware. The periodic cadence is the
    only source of REFRESH writes now. After N consecutive timeouts the loop
    raises TransportError instead of looping forever."""
    t = _TimeoutTransport()
    buf = io.StringIO()
    sink = CsvSink(buf)
    from fnb58_logger.transport import TransportError
    with pytest.raises(TransportError):
        run_logger(t, sink, drain_max_ms=50, max_consecutive_timeouts=2)
    sink.close()

    # Only the three startup writes. No REFRESH-on-timeout spam.
    assert t.writes == [PROBE_1, PROBE_2, PROBE_2]


def test_drain_fifo_runs_on_exit():
    """run_logger must drain the device IN FIFO in its finally block so the
    meter doesn't freeze after we disconnect."""
    t = _TimeoutTransport()
    buf = io.StringIO()
    sink = CsvSink(buf)
    from fnb58_logger.transport import TransportError
    with pytest.raises(TransportError):
        run_logger(t, sink, drain_max_ms=50, max_consecutive_timeouts=2)
    sink.close()

    # The read loop reads at least max_consecutive_timeouts times, then
    # drain_fifo adds at least one more read on the exception path.
    assert t.reads_called >= 3


def test_row_as_dict_roundtrip():
    s = Sample(voltage=5.0, current=1.0, dp=5.0, dn=0.0, temp_c=25.0, flag=1)
    row = Row(
        timestamp=1700000000.123,
        t_elapsed=0.01,
        packet=0,
        sample=1,
        s=s,
        temp_c_ema=24.8,
        energy_ws=0.05,
        capacity_as=0.01,
    )
    d = row.as_dict()
    assert set(d.keys()) == set(CSV_COLUMNS)
    assert d["power_W"] == pytest.approx(5.0)
    # state defaults to empty string when no marker provider is wired in.
    assert d["state"] == ""
