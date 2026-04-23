# Windows: Zadig + WinUSB driver swap

You only need this path if the default (hidapi) backend hits a cython-hidapi
overlapped-I/O bug on your Windows box. Symptom: `fnb58-log` opens the
device but then raises
`hid_write/WaitForSingleObject: Overlapped I/O operation is in progress`.

## Install Zadig and swap the driver

1. Download Zadig from https://zadig.akeo.ie and run it.
2. **Options → List All Devices.** You should see a device with VID
   `2E3C` (FNB58/FNB48S) or `0483` (FNB48/C1).
3. Select the FNIRSI device. The left-hand driver should say
   `HidUsb` (that's why hidapi works by default; that's also why the
   pyusb path can't grab the interface until we swap).
4. In the right-hand driver dropdown pick **WinUSB**. Click
   **Replace Driver**. This takes ~15 seconds.

## Use the pyusb backend

```powershell
python -m pip install pyusb
fnb58-log --backend pyusb
```

## Reverting

The driver swap is per-device and survives reboots. To go back to the
native HID driver:

1. **Device Manager** → find the FNIRSI entry under *Universal Serial
   Bus devices* (it'll be there, not under HID, after the Zadig swap).
2. Right-click → **Uninstall device** → check "Delete the driver
   software for this device".
3. Unplug and replug the meter. Windows reinstalls the default HID
   driver.

## Multi-machine tip

If you use the FNB58 across several Windows 11 boxes, pick one
convention and stick to it. Mixing "some machines hid, some machines
winusb" is a rich source of CI-style "works on mine" bugs because the
command line differs (`--backend pyusb` vs nothing). The hid path with
no Zadig swap is the more portable default.
