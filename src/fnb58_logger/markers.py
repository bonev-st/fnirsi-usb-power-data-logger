"""UART state-marker reader.

The firmware under test emits lines of the form::

    #STATE:DEEP_SLEEP
    #STATE:RADIO_TX
    #STATE:IDLE

over a UART. ``MarkerReader`` opens the port in a background thread and
exposes the last-seen label via :meth:`current_state`, which the logger
writes into the ``state`` CSV column.

pyserial is imported lazily so it remains an optional dependency — install
with ``pip install fnb58-logger[markers]`` (or just ``pip install pyserial``).
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from typing import Any


_STATE_LINE = re.compile(rb"#STATE\s*:\s*([A-Za-z0-9_.\-]{1,32})")


class MarkerReader:
    """Background reader for firmware ``#STATE:NAME`` markers."""

    def __init__(
        self,
        port: str,
        *,
        baud: int = 115200,
        on_warn: Callable[[str], None] | None = None,
    ) -> None:
        try:
            import serial  # type: ignore
        except ImportError as e:  # pragma: no cover - exercised by users without pyserial
            raise ImportError(
                "pyserial is required for --marker-port. "
                "Install with `pip install pyserial`."
            ) from e
        self._serial_mod: Any = serial
        self._port = port
        self._baud = baud
        self._on_warn = on_warn or (lambda _msg: None)
        self._state = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ser: Any | None = None

    def current_state(self) -> str:
        with self._lock:
            return self._state

    def start(self) -> None:
        if self._thread is not None:
            return
        # Opened here (not in the thread) so port errors surface synchronously.
        self._ser = self._serial_mod.Serial(
            self._port, self._baud, timeout=0.1,
        )
        self._thread = threading.Thread(
            target=self._run, name="fnb58-marker-reader", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:  # pragma: no cover
                pass
            self._ser = None

    def _run(self) -> None:
        assert self._ser is not None
        buf = bytearray()
        while not self._stop.is_set():
            try:
                chunk = self._ser.read(128)
            except Exception as e:  # pragma: no cover
                self._on_warn(f"marker-reader: read failed: {e}")
                return
            if not chunk:
                continue
            buf.extend(chunk)
            while True:
                nl = buf.find(b"\n")
                if nl < 0:
                    break
                line = bytes(buf[:nl])
                del buf[: nl + 1]
                self._handle_line(line)

    def _handle_line(self, line: bytes) -> None:
        m = _STATE_LINE.search(line)
        if not m:
            return
        name = m.group(1).decode("ascii", errors="replace")
        with self._lock:
            self._state = name
