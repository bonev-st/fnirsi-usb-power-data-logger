"""Transport abstraction layer.

The device is HID-class on the USB bus but the reference implementation talks
to it via raw interrupt transfers (pyusb). On Windows that approach requires
replacing the HID driver via Zadig, which is awful for end users. So the
primary backend is hidapi — it uses the OS-native HID stack on both platforms
— and pyusb is kept as an optional fallback.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..protocol import DEVICE_IDS, Model


class TransportError(RuntimeError):
    """Raised when the underlying USB stack fails."""


@dataclass(frozen=True)
class DeviceInfo:
    vid: int
    pid: int
    model: Model
    path: str | None = None  # opaque, backend-specific


class Transport(ABC):
    """Read/write 64-byte frames to a FNIRSI power meter."""

    info: DeviceInfo

    @abstractmethod
    def write(self, frame: bytes) -> None: ...

    @abstractmethod
    def read(self, timeout_ms: int = 1000) -> bytes | None:
        """Return one 64-byte frame, or None on timeout."""

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _known_ids() -> list[tuple[int, int, Model]]:
    return [(vid, pid, model) for (vid, pid), model in DEVICE_IDS.items()]


def open_transport(backend: str = "auto") -> Transport:
    """Open the first detected FNIRSI meter using the requested backend.

    backend: "auto" | "hid" | "pyusb"
    """
    if backend not in {"auto", "hid", "pyusb"}:
        raise ValueError(f"unknown backend {backend!r}")

    errors: list[str] = []

    if backend in {"auto", "hid"}:
        try:
            from .hid_backend import HidTransport
            return HidTransport.open_first(_known_ids())
        except TransportError as e:
            if backend == "hid":
                raise
            errors.append(f"hid: {e}")
        except ImportError as e:
            if backend == "hid":
                raise TransportError(
                    "hidapi backend selected but the `hid` package is not installed"
                ) from e
            errors.append(f"hid: {e}")

    if backend in {"auto", "pyusb"}:
        try:
            from .usb_backend import UsbTransport
            return UsbTransport.open_first(_known_ids())
        except TransportError as e:
            if backend == "pyusb":
                raise
            errors.append(f"pyusb: {e}")
        except ImportError as e:
            if backend == "pyusb":
                raise TransportError(
                    "pyusb backend selected but the `pyusb` package is not installed "
                    "(install with `pip install fnb58-logger[pyusb]`)"
                ) from e
            errors.append(f"pyusb: {e}")
        except Exception as e:
            # pyusb's NoBackendError (missing libusb DLL on Windows) is a
            # ValueError subclass and doesn't fit the above categories. Treat
            # any unexpected backend failure as "this backend didn't work"
            # rather than letting it bubble up as an uncaught traceback.
            if backend == "pyusb":
                raise TransportError(f"pyusb backend failed: {e}") from e
            errors.append(f"pyusb: {e}")

    raise TransportError("no transport could open the device: " + "; ".join(errors))
