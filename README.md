# Contec CMS50F USB-C downloader

Download the overnight recordings from a **Contec CMS50F** (or CMS50E) wrist pulse oximeter with a **USB-C** socket,
and save them as:

- **`.SpO2` files that OSCAR can import**, so you can see oximetry next to your CPAP data, and
- **CSV files** you can open in a spreadsheet or analyse yourself.

It is one Python script, `cms50f_download.py`. It runs on Windows, macOS and Linux and needs no driver.

## Why this exists

Contec has sold two different generations of these oximeters under the same names
(CMS50F, CMS50E, Pulox PO-400, Beurer PO 80). They look alike, but they talk to the PC in completely different ways.

| | Older models | Newer models (USB-C) |
|---|---|---|
| Connector | Micro-USB-looking socket. The cable has a USB-to-serial chip built in. | **USB-C**, with an ordinary cable |
| How the PC sees it | A serial **COM port** (Silicon Labs CP210x, sometimes PL2303 or CH340). Needs a driver. | A **USB HID device**, ID `28E9:028A`. It appears under "Human Interface Devices". No COM port, no driver. |
| Version shown by the watch | "X.Y", for example firmware 3.7 or 4.6 | "X.Y.Z", for example hardware 2.0.0, firmware 2.0.x |
| Works with | Contec SpO2 Assistant, OSCAR | Contec Smart Device Assistant (Windows only, no longer offered), [pulseoxdl](https://codeberg.org/klimd/pulseoxdl) (Linux), **this script** |

The problem for owners of the USB-C model:

- **Contec's SpO2 Assistant is no longer available.** It was the usual way to get data off these watches,
  and it only ever supported the older serial models anyway.
- **OSCAR cannot read the USB-C model.** OSCAR's CMS50 importer only opens serial COM ports.
  The USB-C watch has no COM port, so OSCAR never finds it, however you set it up.
- **Contec's newer tool, Smart Device Assistant, can't be downloaded either.** It isn't offered on Contec's website.
  It was Windows-only, and it deleted Auto-mode recordings from the watch after downloading them.

This script talks to the USB-C watch directly, over USB HID, and leaves the recordings on the watch.
It saves each recording in the same `.SpO2` file format Contec's own software wrote,
which OSCAR already knows how to import. See [Import into OSCAR](#import-into-oscar).

## What it does

When you run it, the script:

1. Finds the oximeter on USB (vendor `28E9`, product `028A`).
2. Repeats the start-up sequence Contec's own software uses. **This sets the watch clock to your PC's clock.**
3. Lists the recordings stored on the watch, in either recording mode:
   - **Auto** mode: up to 99 recordings.
   - **Manual** mode: a single recording.
4. Downloads each recording's SpO2 and pulse values, one per second, and checks every packet's checksum and order.
5. Saves each recording as a `.SpO2` file for OSCAR and a `.csv` file. A recording that was already downloaded is skipped.

It **never deletes** anything from the watch. Delete old recordings on the watch itself when you no longer need them.
Live streaming of readings is not supported.

## Requirements

- Python 3.8 or newer. On Windows, install it from [python.org](https://www.python.org/downloads/) and use the `py` command.
- One pip library:

| Library | Why | Install |
|---|---|---|
| `hidapi` | Talks to USB HID devices on Windows, macOS and Linux. Imported as `hid`. | `py -m pip install hidapi` |
| `pyinstaller` | Optional. Only needed to build a stand-alone `.exe` with `build_exe.bat`. | `py -m pip install pyinstaller` |

Everything else comes with Python. You can also install from the requirements file:

```bash
py -m pip install -r requirements.txt
```

There is a different package on PyPI called `hid`, which also installs a module named `hid`.
It does not work with this script. If you have it installed, the script tells you to replace it:

```bash
py -m pip uninstall hid
```

```bash
py -m pip install hidapi
```

On Linux and macOS, use `python3` instead of `py`.

## Usage

1. Plug the watch into the PC with a USB-C **data** cable and switch it on.
2. Close Smart Device Assistant if it is running. Only one program can use the watch at a time.
3. Run the script from the folder it is in:

```bash
py cms50f_download.py
```

Example output:

```text
Connected: model 50F, user 'user'.
Watch clock set to 15-Mar-25 07:30:12.
2 record(s) on the watch:
  14-Mar-25 22:40:58  0 h 00 min  (Auto, 45 samples)
  14-Mar-25 22:41:05  7 h 12 min  (Auto, 25920 samples)
  CMS50F_2025-03-14_22-40-58: downloading...
  CMS50F_2025-03-14_22-40-58: saved .csv and .SpO2, 45 seconds, 80% valid.
  CMS50F_2025-03-14_22-41-05: downloading...
  CMS50F_2025-03-14_22-41-05: saved .csv and .SpO2, 25920 seconds, 97% valid.
Files are in C:\oximeter\CMS50F
```

Options:

| Option | What it does |
|---|---|
| `--list` | Only list the recordings. Nothing is downloaded. |
| `--out FOLDER` | Save the files in FOLDER. By default they go in a `CMS50F` folder next to the script. |
| `--force` | Download recordings again even if their files already exist. |
| `--debug` | Also write every USB report sent and received to `debug-<date>-<time>.log` in the output folder. Attach this file if you report a problem. |

On Linux, your user needs permission to open the device.
pulseoxdl's [udev rule](https://codeberg.org/klimd/pulseoxdl/src/branch/master/udev) does this, or run the script with `sudo`.

### Stand-alone Windows program

To make a `.exe` that runs on a PC without Python, run `build_exe.bat` on a Windows PC that has Python.
The result is `dist\cms50f-download.exe`. It accepts the same options and waits for Enter before closing its window.

## Import into OSCAR

OSCAR can't talk to the USB-C watch, but it can import the `.SpO2` files this script saves.
They are byte-for-byte the same format Contec's software produced, which OSCAR has read for years.
Files from a real USB-C CMS50F have been imported into OSCAR successfully this way.

1. Import your CPAP data for that night into OSCAR first, so OSCAR has a session to line the oximetry up with.
2. In OSCAR, open **Data > Oximetry Wizard**, or press **F7**.
3. Choose **Import from a datafile saved by another program, like SpO2Review**.
   The oximeter type selected at the top doesn't matter for file import.
4. Pick the recording's `CMS50F_….SpO2` file. The file dialog lists `.SpO2` files.
5. When OSCAR asks when the recording started, choose one of these:
   - **I want to use the time reported by my oximeter's built in clock.** Use this if the watch clock was right.
     The script sets the watch clock every time it connects, so recordings made after a download have the PC's time.
   - **I started this oximeter recording at (or near) the same time as a session on my CPAP device.**
     Then choose the CPAP session. Use this if you put the probe on when you started the CPAP.
6. Click **Save and Finish**, or **Sync and Save** if you synced to a CPAP session.

The oximetry then appears on that night's Daily page under the CPAP graphs: SpO2, pulse, and OSCAR's desaturation and pulse-change event flags.
OSCAR imports one file at a time, so repeat the steps for each night.

Very short recordings, such as a few seconds from switching the watch on by accident, are best skipped.

## Output format

Each recording is saved twice, in files named after its start time:

- `CMS50F_YYYY-MM-DD_HH-MM-SS.SpO2` is Contec's binary format, for OSCAR.
  It holds a fixed header, the start date and time, the number of samples,
  then one SpO2 byte and one pulse byte per second. Invalid readings are kept as the watch stored them,
  as SpO2 127 and pulse 255, and OSCAR treats that pair as "no reading".
- `CMS50F_YYYY-MM-DD_HH-MM-SS.csv` is plain text, for spreadsheets and your own analysis:

```text
time,spo2,pulse
2025-03-14 22:41:05,95,71
2025-03-14 22:41:06,95,70
2025-03-14 22:41:07,,
```

- One row per second, oldest first.
- `time` is the watch's local time when the sample was taken, with no time zone.
- `spo2` is oxygen saturation in %. `pulse` is beats per minute.
- A cell is empty when the reading was invalid, for example when the finger was out of the probe.
  The watch stores those as SpO2 127 and pulse 255 (or 0). SpO2 outside 1 to 100 is also left empty.

## Things to know

- **The watch clock.** The clock is set from the PC every time you connect.
  A recording keeps whatever time the watch had when it was recorded, so recordings made before the
  first connection may be off. pulseoxdl has a [script to shift timestamps](https://codeberg.org/klimd/pulseoxdl/src/branch/master/utils/reset-timestamps.sh) if you need it.
- **Manual mode.** The watch stores the first 27 seconds or so as invalid, even with the finger in.
- **Short recordings.** Every recording on the watch is downloaded, including ones under a minute long from
  switching the watch on by accident. Delete or ignore them as you like.

## Testing

`tests/replay_hid.py` plays real USB traffic, recorded by the pulseoxdl project, through the script.
The recordings cover one Auto-mode and one Manual-mode recording.
The test compares the script's output with the files Contec's own software produced from the same data:

- The CSV must match Contec's CSV row by row.
- The `.SpO2` file must be byte-for-byte identical to Contec's `.SpO2` file.
- The `.SpO2` file must also read back correctly through a Python copy of OSCAR's `.SpO2` reader.

It also fails if the script sends any command it does not expect, such as a delete.

```bash
git clone https://codeberg.org/klimd/pulseoxdl.git
```

```bash
python3 tests/replay_hid.py pulseoxdl
```

Expected result:

```text
auto   CMS50F_2021-05-15_12-06-42.csv: 10425 rows, 0 mismatches
auto   CMS50F_2021-05-15_12-06-42.SpO2: identical to maker's file: True, OSCAR-style parse: 10425 samples from 2021-05-15 12:06:42, ok
manual CMS50F_2025-07-05_16-59-23.csv: 2050 rows, 0 mismatches
manual CMS50F_2025-07-05_16-59-23.SpO2: identical to maker's file: True, OSCAR-style parse: 2050 samples from 2025-07-05 16:59:23, ok
PASS
```

## How the protocol works

A short summary, for anyone who wants to port or extend this.
The full description is in the [pulseoxdl README](https://codeberg.org/klimd/pulseoxdl).

- Reports are 64 bytes each way and are not numbered.
  When writing with hidapi, a leading `0x00` report-ID byte is added, so 65 bytes are written.
- Every command and reply ends in a checksum: the sum of the earlier bytes, kept to 7 bits.
  The first "stop" command is the only exception.
- Start-up, as Contec's software does it. Each line shows the command, then the first byte and length of the reply.
  1. `7D 81 A7 80×6 7D 81 A2 80×6` stops any live stream. Reply `F0`, 2 bytes.
  2. `82 02`. Reply `F2`, 8 bytes.
  3. `80 00`. Reply `F0`, 2 bytes.
  4. `83 yy mm dd HH MM SS 00 00 ck` sets the watch clock. Reply `F3`, 3 bytes.
  5. `8E 03 ck` reads the user name. Reply `FE`, 10 bytes.
  6. `81 ck` reads the model, for example "50F". Reply `F1`, 10 bytes.
- `9F 1F` asks what is stored. Reply `EF`, 8 bytes.
  Byte 2 is not zero if recordings exist. Byte 3 is not zero for Auto mode, zero for Manual mode.
- Auto-mode recording list: send `9C 01 ck` repeatedly. Reply `EC`, 21 bytes.
  Byte 1 is `0x40` on the last recording, byte 3 is the recording number, bytes 4–9 are the start time,
  and the length in seconds is `b10 + b11<<7 + b12<<14`.
- Manual-mode recording: `A0 00 ck`. Reply `D0`, 14 bytes. Start time in bytes 2–7, length in bytes 10–12.
- Data requests. SpO2 and pulse are fetched separately:
  - Auto: `9D 04 <01 SpO2 | 02 pulse> 01 <recording number> 00 00 00 ck`.
    The data comes in 30-byte packets.
  - Manual: `<A3 SpO2 | A2 pulse> 00 00 00 ck`. The data comes in 20-byte packets.
  - Packets run back to back across the 64-byte reports, so one packet can be split over two reports.
  - Values are packed two to a byte, as differences from a running value. See the two decoder functions in the script.

## Credits and licence

The protocol was worked out by Donatas Klimašauskas in [pulseoxdl](https://codeberg.org/klimd/pulseoxdl),
by watching Contec's software talk to the watch. This script is a Python port of that work.
pulseoxdl is licensed under the GPL, version 3 or later, and so is this project. See [LICENSE](LICENSE).

This project is not affiliated with Contec. It is not a medical device.
Use the data for personal interest, and talk to a doctor about anything that worries you.
