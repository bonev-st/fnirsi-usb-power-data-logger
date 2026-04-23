# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
  when stderr is a tty — so piping into another tool stays clean. Owns the
  `--summary` JSON emission and the optional `MarkerReader` lifetime.
- `src/fnb58_logger/markers.py` — optional UART state-marker reader. Lazy
  imports pyserial (`markers` extra), runs a background thread, and exposes
  `current_state()` for `run_logger` to call once per row. Parses
  `#STATE:NAME` lines case-sensitively; label chars are
  `[A-Za-z0-9_.\-]{1,32}`.

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
- FNB58 (firmware 1.11) freeze modes observed in the field:
  - `PROBE_1` and `PROBE_2` appear to be unnecessary for initial
    initialization and for subsequent `REFRESH`s — the device streams
    fine without them.
  - Running the full `PROBE_1 → PROBE_2 → PROBE_2` startup can cause the
    meter to freeze after several open/close cycles, requiring a
    power-cycle of the device.
  - Sending consecutive frames too quickly (back-to-back writes with no
    gap) can also wedge the FNB58.
  When touching `startup_sequence()` or the write cadence in
  `SampleStream.run()`, keep these in mind — err toward fewer probe
  frames and a small inter-frame delay.

## Power measurement workflow

This repo is used as a test harness for embedded power-consumption work:
flash firmware → run a scenario on the DUT → capture a labeled trace →
compare against a baseline tied to a git SHA.

### Recording a run

```bash
# 30 s run, CSV + JSON summary on stderr (stdout still has CSV)
fnb58-log -o runs/<sha>-<label>.csv -d 30 --summary

# With a firmware UART emitting `#STATE:NAME` lines
fnb58-log -o runs/<sha>-<label>.csv -d 30 --summary \
    --marker-port /dev/ttyUSB0 --marker-baud 115200
```

Convention: outputs go under `runs/`, named
`runs/<short-sha>[-dirty]-<label>.csv`. A sibling
`runs/<short-sha>-<label>.summary.json` carries the one-line summary
(from `--summary`) and a `.meta.json` carries branch / hostname /
firmware tags. The `/measure` slash command (see
`claude-commands/measure.md`) handles all of this.

### --summary JSON shape

```
{"samples": 3000, "duration_s": 30.0,
 "mean_V": 3.30000, "min_V": 3.29, "max_V": 3.31,
 "mean_mA": 12.345, "median_mA": 10.5, "p95_mA": 45.2, "min_mA": 0.01, "max_mA": 80.4,
 "mean_mW": 40.73, "median_mW": 34.65, "p95_mW": 149.16,
 "energy_mWh": 0.3394, "charge_mAh": 0.1029,
 "states": ["DEEP_SLEEP", "RADIO_TX"]}
```

Everything is in SI-friendly units: mA, mW, mWh, mAh. `samples` is the
number of decoded 10 ms samples; `duration_s` is `last_t_elapsed -
first_t_elapsed` (device clock, not wall clock).

### UART state markers

`--marker-port` spawns a background thread that parses lines of the form
`#STATE:NAME\n` from the serial port. The most recently seen label is
written to the `state` column of every subsequent CSV row (and tracked in
`summary.states`). `scripts/compare.py --state NAME` can then restrict
metrics to just the rows in a given firmware state — use this to compare
energy-per-state across runs.

The firmware side is trivial: `printf("#STATE:DEEP_SLEEP\r\n")` on entry
to each state. Don't emit markers faster than you care about — each
marker has no intrinsic energy cost, but if you print a long banner the
UART peripheral staying powered for longer will skew the surrounding
samples.

### CSV state column

The `state` column is always present (14 columns total; see
`CSV_COLUMNS`). It is empty when `--marker-port` is not used. Any parser
that reads by column name is unaffected; positional parsers get one more
trailing field.

### compare.py conventions

`scripts/compare.py baseline.csv candidate.csv` prints a table of
metrics with absolute and percent change. `--state NAME` restricts to
that state. `--json` emits machine-readable output for piping. Exit code
is always 0 — this is a reporting tool, not a CI gate. Energy and
charge for state-filtered diffs are integrated from the retained rows
(10 ms each), not read from the cumulative CSV columns, because those
accumulators are global across the run.

## Reference

- Upstream: https://github.com/baryluk/fnirsi-usb-power-data-logger
- Planning docs in repo root: `task_plan.md`, `findings.md`, `progress.md`.
