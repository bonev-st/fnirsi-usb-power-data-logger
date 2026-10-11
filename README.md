# fnb58-logger

Cross-platform (Windows 11 + Linux) Python CLI that reads the 100 Hz data
stream from a **FNIRSI FNB58** USB power meter and writes CSV. Also works
with FNB48, FNB48S, and C1.

The protocol and frame layout are derived from
[baryluk/fnirsi-usb-power-data-logger](https://github.com/baryluk/fnirsi-usb-power-data-logger).
The difference here is a clean install on Windows (no Zadig), a pyusb
fallback, a proper package layout, and pytest coverage.

## Install

Recommended — works the same on Windows 11 and Linux, no per-machine
virtualenv to manage:

```bash
# uv (https://docs.astral.sh/uv/) — installs fnb58-log into a managed venv
uv tool install .

# or pipx
pipx install .
```

From a clone, for development:

```bash
python -m pip install -e ".[dev]"
# optional extras:
python -m pip install -e ".[pyusb]"    # pyusb fallback backend
python -m pip install -e ".[markers]"  # --marker-port UART state support
```

Requires Python 3.10+.

### Claude Code skills (optional)

Claude Code picks up the `/power-measure`, `/power-validate`,
`/power-baseline`, `/power-compare` and `/power-host` skills from
`.claude/skills/` automatically when launched in this repo. See
[`.claude/README.md`](.claude/README.md) for user-global install and
[`scenarios/README.md`](scenarios/README.md) for scenario files. The
older `claude-commands/` prompt templates are deprecated.

## Usage

```bash
fnb58-log                        # CSV to stdout (pipe into anything)
fnb58-log -o session.csv         # CSV to file; live status line on stderr
fnb58-log -o session.csv -d 60   # stop after 60 s
fnb58-log --summary              # one-line JSON metrics at end of run
fnb58-log --marker-port COM5     # tag samples with firmware #STATE: labels
fnb58-log --backend pyusb        # force the pyusb backend
fnb58-log --crc                  # verify CRC-8 on every frame
fnb58-log -v                     # print device info and warnings
```

Run `fnb58-log -h` for the full flag list.

### Comparing runs

```bash
python scripts/compare.py baseline.csv candidate.csv
python scripts/compare.py baseline.csv candidate.csv --state DEEP_SLEEP
python scripts/compare.py baseline.csv candidate.csv --json
```

`compare.py` prints a table of mean / median / p95 current and power,
total energy (mWh) and charge (mAh), and absolute + percent change from
A to B. With `--state`, the comparison is restricted to rows the
firmware tagged with that label (via `--marker-port`).

### CSV columns

`timestamp, t_elapsed, packet, sample, voltage_V, current_A, power_W,
dp_V, dn_V, temp_C, temp_C_ema, energy_Ws, capacity_As, state`

- `timestamp`: absolute seconds since epoch (float).
- `t_elapsed`: 10 ms-aligned elapsed time from the first sample.
- `energy_Ws` / `capacity_As`: cumulative since start (Ws, As).
- `temp_C_ema`: low-pass filter of `temp_C` (α = 0.9).
- `state`: firmware-reported state from `--marker-port`; empty when
  unused.

## Platform notes

### Windows 11

Default backend (**hidapi**) works with the native HID driver — no Zadig
needed. If you hit `hid_write/WaitForSingleObject: Overlapped I/O operation
is in progress`, that's a known cython-hidapi 1.x quirk on Windows. Fall back
to pyusb — full walkthrough at [`docs/zadig-notes.md`](docs/zadig-notes.md).
Short version:

1. Install [Zadig](https://zadig.akeo.ie/), select the FNB58 interface and
   replace its driver with **WinUSB**.
2. `pip install pyusb`
3. `fnb58-log --backend pyusb`

After the driver swap the device is no longer a normal HID device — to
restore the FNIRSI default, use Windows Device Manager → *Uninstall device*
→ replug.

```bash
set PYTHONPATH=src
.venv/bin/python -m fnb58_logger -v -d 10 --summary
```

### Linux

Install libusb and add a udev rule so you don't need `sudo`. Copy
`udev/99-fnirsi.rules` to `/etc/udev/rules.d/` and reload:

```bash
sudo cp udev/99-fnirsi.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Then replug the meter. Both backends should work; hidapi talks to
`/dev/hidraw*`, pyusb talks to the raw interface.

### FNB58 firmware 1.11 freeze note

On FNB58 (firmware 1.11) the `PROBE_1` and `PROBE_2` frames appear to be
unnecessary — neither for the initial handshake nor before a subsequent
`REFRESH`. A few failure modes have been observed:

- Running the full `PROBE_1 → PROBE_2 → PROBE_2` startup can cause the
  meter to freeze after several startups, requiring a power-cycle of the
  device.
- Sending consecutive frames too quickly (too short an interval between
  writes) can also wedge the FNB58.

If you see the logger lock up across repeated runs, try dropping the
probes and/or adding a small delay between writes.

```bash
PYTHONPATH=src .venv/bin/python -m fnb58_logger -v -d 10 --summary
```

### Raspberry Pi 4 note

The VL805 xHCI controller that drives all four external USB ports on the
Pi 4 has a silicon/firmware errata that hits full-speed HID devices on
certain downstream ports. The FNB58 is a full-speed device, so it's
susceptible. Symptom: the logger opens the device but is stuck on
`[warn] read timeout; sending refresh`, and `dmesg` shows entries like:

```
xhci_hcd 0000:01:00.0: ERROR: unexpected setup address command completion code 0x11
usb 1-1.2: WARN: invalid context state for evaluate context command
usb 1-1.2: unable to enumerate USB device
```

This is unfixable from user space and is not avoided by the latest VL805
firmware or kernel. Workaround: **move the FNB58 to a different physical
USB port**. The two bottom (black) USB 2 ports tend to avoid the problem;
the top (blue) USB 3 ports may or may not, depending on the individual
board's port-to-hub mapping.

## Development

```bash
python -m pytest                 # runs 14 hardware-free tests
ruff check src tests
```

The protocol decoder, CRC, and core logging loop are covered by synthetic
packets — no hardware required for CI.

## Supported devices

| Device  | VID    | PID    |
|---------|--------|--------|
| FNB48   | 0x0483 | 0x003A |
| C1      | 0x0483 | 0x003B |
| FNB58   | 0x2E3C | 0x5558 |
| FNB48S  | 0x2E3C | 0x0049 |

## License

MIT.
