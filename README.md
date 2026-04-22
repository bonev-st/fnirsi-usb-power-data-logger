# fnb58-logger

Cross-platform (Windows 11 + Linux) Python CLI that reads the 100 Hz data
stream from a **FNIRSI FNB58** USB power meter and writes CSV. Also works
with FNB48, FNB48S, and C1.

The protocol and frame layout are derived from
[baryluk/fnirsi-usb-power-data-logger](https://github.com/baryluk/fnirsi-usb-power-data-logger).
The difference here is a clean install on Windows (no Zadig), a pyusb
fallback, a proper package layout, and pytest coverage.

## Install

```bash
python -m pip install -e ".[dev]"     # from a clone
# optional:
python -m pip install -e ".[pyusb]"   # enables the pyusb fallback backend
```

Requires Python 3.10+.

## Usage

```bash
fnb58-log                        # CSV to stdout (pipe into anything)
fnb58-log -o session.csv         # CSV to file; live status line on stderr
fnb58-log -o session.csv -d 60   # stop after 60 s
fnb58-log --backend pyusb        # force the pyusb backend
fnb58-log --crc                  # verify CRC-8 on every frame
fnb58-log -v                     # print device info and warnings
```

Run `fnb58-log -h` for the full flag list.

### CSV columns

`timestamp, t_elapsed, packet, sample, voltage_V, current_A, power_W,
dp_V, dn_V, temp_C, temp_C_ema, energy_Ws, capacity_As`

- `timestamp`: absolute seconds since epoch (float).
- `t_elapsed`: 10 ms-aligned elapsed time from the first sample.
- `energy_Ws` / `capacity_As`: cumulative since start (Ws, As).
- `temp_C_ema`: low-pass filter of `temp_C` (α tunable via `--alpha`).

## Platform notes

### Windows 11

Default backend (**hidapi**) works with the native HID driver — no Zadig
needed. If you hit `hid_write/WaitForSingleObject: Overlapped I/O operation
is in progress`, that's a known cython-hidapi 1.x quirk on Windows. Fall back
to pyusb:

1. Install [Zadig](https://zadig.akeo.ie/), select the FNB58 interface and
   replace its driver with **WinUSB**.
2. `pip install pyusb`
3. `fnb58-log --backend pyusb`

After the driver swap the device is no longer a normal HID device — to
restore the FNIRSI default, use Windows Device Manager → *Uninstall device*
→ replug.

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
