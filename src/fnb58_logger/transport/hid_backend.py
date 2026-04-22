"""hidapi backend (primary).

Works with the native HID driver on both Windows and Linux, so no Zadig /
driver-replacement dance on Windows. The `hid` PyPI package (cython-hidapi)
exposes a thin wrapper over the hidapi C library.

Note the report-ID quirk: hidapi's write() expects the first byte to be a
report ID. The FNIRSI protocol has no meaningful report ID, so we prepend
0x00. Reads return the raw 64-byte frame as-is.
"""

from __future__ import annotations

from ..protocol import Model, PACKET_SIZE
from .base import DeviceInfo, Transport, TransportError


class HidTransport(Transport):
    def __init__(self, dev, info: DeviceInfo) -> None:
        self._dev = dev
        self.info = info

    @classmethod
    def open_first(cls, known_ids: list[tuple[int, int, Model]]) -> "HidTransport":
        try:
            import hid  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover — covered in base.open_transport
            raise ImportError("install the `hid` package (cython-hidapi)") from e

        # hid.enumerate() returns every HID interface; we want the first match.
        for vid, pid, model in known_ids:
            for entry in hid.enumerate(vid, pid):
                path = entry.get("path")
                try:
                    dev = hid.Device(path=path) if path else hid.Device(vid, pid)
                except Exception as e:  # pragma: no cover
                    raise TransportError(f"failed to open {vid:04x}:{pid:04x}: {e}") from e
                info = DeviceInfo(
                    vid=vid,
                    pid=pid,
                    model=model,
                    path=path.decode(errors="replace") if isinstance(path, bytes) else path,
                )
                return cls(dev, info)

        raise TransportError(
            "no FNIRSI power meter found (checked "
            + ", ".join(f"{v:04x}:{p:04x}" for v, p, _ in known_ids)
            + ")"
        )

    def write(self, frame: bytes) -> None:
        if len(frame) != PACKET_SIZE:
            raise ValueError(f"frame must be {PACKET_SIZE} bytes, got {len(frame)}")
        # Prepend report ID 0x00.
        self._dev.write(b"\x00" + frame)

    def read(self, timeout_ms: int = 1000) -> bytes | None:
        data = self._dev.read(PACKET_SIZE, timeout=timeout_ms)
        if not data:
            return None
        buf = bytes(data)
        # Some hidapi builds prepend the report ID on read too; strip it if present.
        if len(buf) == PACKET_SIZE + 1 and buf[0] == 0x00:
            buf = buf[1:]
        return buf

    def close(self) -> None:
        try:
            self._dev.close()
        except Exception:
            pass
