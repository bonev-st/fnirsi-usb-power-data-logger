# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What this repo is

Cross-platform Python CLI (`fnb58-log`) that logs the 100 Hz stream from a
FNIRSI FNB58 (and FNB48 / FNB48S / C1) USB power meter to CSV. The protocol
is reverse-engineered; it is a vendor-proprietary HID-class interface that
exchanges 64-byte interrupt frames — there is no meaningful HID report
descriptor semantics, only raw frames.

## Common commands

```bash
# Editable install with dev deps (pytest, ruff)
python -m pip install -e ".[dev]"

# Run hardware-free tests (fast)
python -m pytest
python -m pytest tests/test_protocol.py::test_decode_data_frame_fields -v

# Lint
ruff check src tests

# Run the logger
fnb58-log                     # CSV to stdout
fnb58-log -o out.csv -v       # to file + device info on stderr
fnb58-log --backend pyusb     # force pyusb fallback (needs Zadig on Windows)
python -m fnb58_logger        # equivalent to `fnb58-log`
```

## Architecture

Three layers. Each has a well-defined seam, keeping I/O out of the decoder
and keeping the CLI thin.

- `src/fnb58_logger/protocol.py` — **pure, hardware-free**. Device ID table,
  CRC-8 (`poly=0x39, init=0x42`), `Sample` dataclass, `decode_packet()`,
  pre-built startup frames (`PROBE_1`, `PROBE_2`, `REFRESH`), and
  `startup_sequence()` (same `PROBE_1 → PROBE_2 → REFRESH` for all models).
  Refresh cadence also lives here (1 s for FNB58/FNB48S, 3 ms for FNB48/C1).
  No I/O allowed in this module — keep it that way so tests can assert the
  full decoder without mocks.
- `src/fnb58_logger/transport/` — the USB seam.
  - `base.py` defines the `Transport` ABC (`write`, `read`, `close`) plus
    `open_transport(backend)` which tries hidapi first, then pyusb.
  - `hid_backend.py` — cython-hidapi (`hid` PyPI package). Prepends report
    ID `0x00` on writes (Windows quirk); strips a leading `0x00` on reads
    if hidapi decided to prepend one.
  - `usb_backend.py` — pyusb fallback (interface match, detach kernel
    driver, find interrupt IN/OUT endpoints dynamically). On Windows this
    path requires the device's driver to be WinUSB (via Zadig) because
    Windows' HIDClass driver holds the interface otherwise.
- `src/fnb58_logger/logger.py` — the runtime loop.
  - `SampleStream.run()` drives the device: startup writes with 100 ms
    spacing, then `read → decode → yield`, periodically re-sending
    `REFRESH`.
  - `run_logger()` wraps the stream: maintains cumulative `energy_Ws` and
    `capacity_As`, the temperature EMA, and writes `Row` objects to a
    `RowSink`. Uses `SAMPLE_INTERVAL_S` (0.01 s) as the clock for integration —
    does not rely on host wall-clock for sample cadence (the device is the
    authority).
  - `CsvSink` implements the only sink today. Header fields are defined once
    in `CSV_COLUMNS` and must stay consistent with `Row.as_dict()`.
- `src/fnb58_logger/__main__.py` — argparse CLI. Writes CSV to stdout when
  `-o` is absent, to the file otherwise. The status line is sent to
  **stderr** only when writing to a file (stdout is busy with CSV) and only
  when stderr is a tty — so piping into another tool stays clean.

## Protocol invariants to preserve

- Frame size is exactly 64 bytes. If `decode_packet` is given anything else
  it must raise `BadPacket`.
- Byte 0 is `0xAA` magic. Byte 1 is the type (`0x04` = data; anything else
  is a non-data frame and `decode_packet` returns `None`).
- CRC-8 is computed over bytes 1..62 and lives at byte 63. Parameters:
  `poly=0x39, init=0x42, refin=false, refout=false, xorout=0x00`. Hand-verified
  test vectors: `AA 81 00×61 8E`, `AA 82 00×61 96`, `AA 83 00×61 9E`.
- Sample unpack format is `"<IIHHBH"` (15 bytes). Scales:
  voltage /100000 V, current /100000 A, D+/D- /1000 V, temperature /10 °C.

## Testing strategy

All tests are hardware-free. `_FakeTransport` in `tests/test_protocol.py`
implements the minimal `Transport` surface and cycles through a list of
frames — `run_logger` terminates via `duration_s`, not by frame exhaustion.
When adding new tests for the runtime loop, follow that pattern.

## Gotchas

- cython-hidapi on Windows can raise
  `hid_write/WaitForSingleObject: Overlapped I/O operation is in progress`
  after a successful open if the device stops streaming. Documented in the
  README; workaround is the pyusb path with Zadig.
- hidapi's Device.write() prepends the report ID byte — always send
  `b"\x00" + frame` from the hid backend, never the raw frame.
- pyusb's kernel-driver detach only applies on Linux; wrap in
  `try/except (NotImplementedError, AttributeError)` (done in usb_backend.py).
- The reference repo (`baryluk/fnirsi-usb-power-data-logger`) uses pyusb
  exclusively. When diffing against it, expect our CSV columns to differ
  (we use `,` not space, and include derived `power_W`) — do not "fix" to
  match the reference.

## Reference
- Upstream: https://github.com/baryluk/fnirsi-usb-power-data-logger
- Planning docs in repo root: `task_plan.md`, `findings.md`, `progress.md`.
