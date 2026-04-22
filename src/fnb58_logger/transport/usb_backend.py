"""pyusb backend (optional fallback).

Mirrors the reference implementation: detach kernel driver on Linux, set
configuration, discover interrupt IN/OUT endpoints dynamically. On Windows
this requires the WinUSB driver on the device (Zadig), otherwise Windows'
HIDClass driver will hold the interface.
"""

from __future__ import annotations

from ..protocol import Model, PACKET_SIZE
from .base import DeviceInfo, Transport, TransportError


class UsbTransport(Transport):
    def __init__(self, dev, ep_in, ep_out, info: DeviceInfo) -> None:
        self._dev = dev
        self._ep_in = ep_in
        self._ep_out = ep_out
        self.info = info

    @classmethod
    def open_first(cls, known_ids: list[tuple[int, int, Model]]) -> "UsbTransport":
        try:
            import usb.core  # type: ignore[import-not-found]
            import usb.util  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError("install pyusb (`pip install pyusb`)") from e

        dev = None
        model = None
        vid = pid = 0
        for v, p, m in known_ids:
            dev = usb.core.find(idVendor=v, idProduct=p)
            if dev is not None:
                vid, pid, model = v, p, m
                break

        if dev is None or model is None:
            raise TransportError("no FNIRSI power meter found via pyusb")

        # Detach any kernel driver that may have claimed interfaces (Linux).
        # On FNB58 the kernel's `hid-generic` binds to the HID interface by
        # default — we need it gone before we can talk to the endpoints.
        try:
            for cfg in dev:
                for intf in cfg:
                    try:
                        if dev.is_kernel_driver_active(intf.bInterfaceNumber):
                            dev.detach_kernel_driver(intf.bInterfaceNumber)
                    except (NotImplementedError, AttributeError):
                        pass  # Windows: no kernel-driver concept at this layer.
        except Exception as e:
            raise TransportError(f"failed to detach kernel driver: {e}") from e

        # Only call set_configuration() if nothing is active. On Linux the
        # device is usually already configured after enumeration, and calling
        # set_configuration on a claimed/configured device fails with ENOENT.
        try:
            active_cfg = dev.get_active_configuration()
        except usb.core.USBError:
            active_cfg = None
        if active_cfg is None:
            try:
                dev.set_configuration()
            except Exception as e:
                raise TransportError(f"set_configuration failed: {e}") from e
            active_cfg = dev.get_active_configuration()

        # FNB58 is a composite device (Mass Storage + CDC + HID). We need
        # the HID interface (class 0x03), which exposes the interrupt
        # IN/OUT endpoints carrying the 64-byte FNIRSI protocol frames.
        HID_CLASS = 0x03
        intf = None
        for candidate in active_cfg:
            if candidate.bInterfaceClass == HID_CLASS:
                intf = candidate
                break
        if intf is None:
            raise TransportError("device has no HID-class interface")

        try:
            usb.util.claim_interface(dev, intf.bInterfaceNumber)
        except Exception as e:
            raise TransportError(
                f"claim_interface({intf.bInterfaceNumber}) failed: {e}"
            ) from e

        def _is_intr(e, direction):
            return (
                usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_INTR
                and usb.util.endpoint_direction(e.bEndpointAddress) == direction
            )

        ep_out = usb.util.find_descriptor(
            intf, custom_match=lambda e: _is_intr(e, usb.util.ENDPOINT_OUT)
        )
        ep_in = usb.util.find_descriptor(
            intf, custom_match=lambda e: _is_intr(e, usb.util.ENDPOINT_IN)
        )
        if ep_in is None or ep_out is None:
            raise TransportError(
                f"interface {intf.bInterfaceNumber}: "
                "could not locate interrupt IN/OUT endpoints"
            )

        info = DeviceInfo(vid=vid, pid=pid, model=model)
        return cls(dev, ep_in, ep_out, info)

    def write(self, frame: bytes) -> None:
        if len(frame) != PACKET_SIZE:
            raise ValueError(f"frame must be {PACKET_SIZE} bytes, got {len(frame)}")
        self._ep_out.write(frame, timeout=1000)

    def read(self, timeout_ms: int = 1000) -> bytes | None:
        try:
            data = self._ep_in.read(PACKET_SIZE, timeout=timeout_ms)
        except Exception as e:
            msg = str(e).lower()
            if "timeout" in msg or "timed out" in msg:
                return None
            raise TransportError(f"read failed: {e}") from e
        return bytes(data)

    def close(self) -> None:
        try:
            import usb.util  # type: ignore[import-not-found]
            usb.util.dispose_resources(self._dev)
        except Exception:
            pass
