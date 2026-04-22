"""FNIRSI FNB58/FNB48/FNB48S/C1 protocol decoder.

All constants and field layouts are derived from the reference implementation at
https://github.com/baryluk/fnirsi-usb-power-data-logger. The hardware speaks raw
64-byte frames over the HID interrupt endpoints (no HID report semantics).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import Enum

PACKET_SIZE = 64
SAMPLES_PER_PACKET = 4
SAMPLE_INTERVAL_S = 0.010  # 100 Hz
PACKET_INTERVAL_S = SAMPLE_INTERVAL_S * SAMPLES_PER_PACKET

MAGIC = 0xAA
PACKET_TYPE_DATA = 0x04


class Model(Enum):
    FNB48 = "FNB48"
    C1 = "C1"
    FNB58 = "FNB58"
    FNB48S = "FNB48S"


# (vid, pid) -> Model
DEVICE_IDS: dict[tuple[int, int], Model] = {
    (0x0483, 0x003A): Model.FNB48,
    (0x0483, 0x003B): Model.C1,
    (0x2E3C, 0x5558): Model.FNB58,
    (0x2E3C, 0x0049): Model.FNB48S,
}


def crc8_fnirsi(data: bytes) -> int:
    """CRC-8 used by FNIRSI frames.

    Params: poly=0x39, init=0x42, refin=false, refout=false, xorout=0x00.
    Computed over bytes 1..62 of the 64-byte frame.
    """
    crc = 0x42
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x39) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def _frame(type_byte: int) -> bytes:
    """Build a 64-byte outbound frame with correct CRC-8 in the last byte."""
    body = bytes([type_byte]) + b"\x00" * 61
    crc = crc8_fnirsi(body)
    return bytes([MAGIC]) + body + bytes([crc])


# Pre-built outbound frames.
PROBE_1 = _frame(0x81)          # AA 81 .. 8E
PROBE_2 = _frame(0x82)          # AA 82 .. 96
REFRESH = _frame(0x83)          # AA 83 .. 9E


def startup_sequence(model: Model) -> list[bytes]:
    """Frames to send, in order, after opening the device."""
    seq = [PROBE_1, PROBE_2]
    if model in (Model.FNB58, Model.FNB48S):
        seq.append(PROBE_2)
    else:  # FNB48, C1
        seq.append(REFRESH)
    return seq


def refresh_interval_s(model: Model) -> float:
    """How often to re-send REFRESH to keep the stream flowing.

    Default mirrors baryluk's reference: 1 s for FNB58/FNB48S, 3 ms for
    FNB48/C1. The "3 ms for everything" variant suggested by upstream
    issue #19 helps some Windows setups but **overwhelms the Pi 4's VL805
    xHCI USB-2 companion** on Linux (333 writes/s trips the TT, the kernel
    resets the port, VL805 gets wedged). Users on the ≥V0.68 firmware who
    see the old 1 s cadence wedge the meter can override this with
    ``--refresh-ms 3`` from the CLI.

    https://github.com/baryluk/fnirsi-usb-power-data-logger/issues/19
    """
    if model in (Model.FNB58, Model.FNB48S):
        return 1.0
    return 0.003


@dataclass(slots=True, frozen=True)
class Sample:
    voltage: float   # volts
    current: float   # amps
    dp: float        # D+ line voltage (V)
    dn: float        # D- line voltage (V)
    temp_c: float    # °C
    flag: int        # byte 12, usually 1

    @property
    def power(self) -> float:
        return self.voltage * self.current


_SAMPLE_STRUCT = struct.Struct("<IIHHBH")  # 15 bytes


def _decode_sample(buf: memoryview) -> Sample:
    v_raw, i_raw, dp_raw, dn_raw, flag, t_raw = _SAMPLE_STRUCT.unpack_from(buf)
    return Sample(
        voltage=v_raw / 100000.0,
        current=i_raw / 100000.0,
        dp=dp_raw / 1000.0,
        dn=dn_raw / 1000.0,
        temp_c=t_raw / 10.0,
        flag=flag,
    )


class BadPacket(ValueError):
    """Raised when a frame fails validation."""


def decode_packet(buf: bytes, *, verify_crc: bool = False) -> list[Sample] | None:
    """Decode a 64-byte inbound frame.

    Returns a list of 4 samples on a data frame (type 0x04).
    Returns None for known non-data frames (ack/status).
    Raises BadPacket for malformed frames or CRC mismatch (when verify_crc=True).
    """
    if len(buf) != PACKET_SIZE:
        raise BadPacket(f"expected {PACKET_SIZE} bytes, got {len(buf)}")
    if buf[0] != MAGIC:
        raise BadPacket(f"bad magic 0x{buf[0]:02X}")
    if buf[1] != PACKET_TYPE_DATA:
        return None
    if verify_crc:
        expected = crc8_fnirsi(buf[1:63])
        if expected != buf[63]:
            raise BadPacket(f"CRC mismatch: expected 0x{expected:02X}, got 0x{buf[63]:02X}")
    mv = memoryview(buf)
    return [_decode_sample(mv[2 + 15 * i : 2 + 15 * (i + 1)]) for i in range(SAMPLES_PER_PACKET)]
