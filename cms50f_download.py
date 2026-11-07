#!/usr/bin/env python3
"""Download stored records from a Contec CMS50F / CMS50E pulse oximeter (USB-C, HID model).

The newer Contec oximeters with a USB-C socket show up on the PC as a USB HID
device (vendor 28E9, product 028A), not as a serial COM port. OSCAR and the old
"SpO2 Assistant" only talk to the serial models, so they cannot see this one.
This script talks to it directly through hidapi and saves every stored record
as a CSV file (one row per second: time, SpO2, pulse) and as a .SpO2 file,
the maker's own binary format, which OSCAR can import (Oximetry > File import).

Usage:
    py cms50f_download.py            # set the watch clock, download all records
    py cms50f_download.py --list     # only list the records on the watch
    py cms50f_download.py --debug    # also write every USB report to a log file
    py cms50f_download.py --out D:\\oximeter --force

Records are never deleted from the watch.

The protocol is ported from pulseoxdl by Donatas Klimasauskas
(https://codeberg.org/klimd/pulseoxdl), which worked it out by watching the
maker's "Smart Device Assistant" software. pulseoxdl is GPL-3.0-or-later, and
so is this file.

Copyright (C) 2021-2025 Donatas Klimasauskas (protocol, pulseoxdl)
Copyright (C) 2026 contributors to this port

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. This program is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details: <https://www.gnu.org/licenses/>.
"""

import argparse
import csv
import datetime as dt
import os
import struct
import sys
import time

__version__ = "1.0.0"

VENDOR_ID = 0x28E9
PRODUCT_ID = 0x028A
REPORT_LEN = 64

# Timeouts in milliseconds.
REPLY_TIMEOUT = 3000   # waiting for the answer to a command
DATA_TIMEOUT = 10000   # waiting for the next report of a data stream
DRAIN_TIMEOUT = 50     # emptying stale reports before a command

# Measurement selectors for the data requests.
AUTO_SPO2, AUTO_PULSE = 0x01, 0x02
MANUAL_SPO2, MANUAL_PULSE = 0xA3, 0xA2

# Values the watch stores while no finger is in the probe (Manual mode).
ARTIFACT_SPO2, ARTIFACT_PULSE = 127, 255

MAX_RECORDS = 99


class DeviceError(Exception):
    """Something went wrong talking to the oximeter."""


def checksum(data):
    """Sum of the bytes, kept to 7 bits. Last byte of every command and reply."""
    return sum(data) & 0x7F


def with_checksum(*data):
    data = bytes(data)
    return data + bytes([checksum(data)])


def hexs(data):
    return " ".join("%02x" % b for b in data)


# --------------------------------------------------------------------------
# Transport: hidapi on a real device. Tests replace it with a fake.
# --------------------------------------------------------------------------

class HidTransport:
    def __init__(self, log=None):
        try:
            import hid  # pip install hidapi
        except ImportError:
            raise DeviceError(
                "The 'hidapi' Python package is missing. Install it with:\n"
                "    py -m pip install hidapi")
        if not hasattr(hid, "device"):
            raise DeviceError(
                "A different package called 'hid' is installed instead of 'hidapi'.\n"
                "Fix it with:\n"
                "    py -m pip uninstall hid\n"
                "    py -m pip install hidapi")
        found = hid.enumerate(VENDOR_ID, PRODUCT_ID)
        if not found:
            raise DeviceError(
                "Oximeter not found (USB HID %04X:%04X).\n"
                "Check the USB-C cable is a data cable, the watch is switched on, "
                "and no other program (Smart Device Assistant) is using it."
                % (VENDOR_ID, PRODUCT_ID))
        self.dev = hid.device()
        try:
            self.dev.open_path(found[0]["path"])
        except (IOError, OSError) as e:
            raise DeviceError("Could not open the oximeter: %s" % e)
        self.log = log
        self.info = "%s %s" % (found[0].get("manufacturer_string") or "",
                               found[0].get("product_string") or "")

    def write(self, data):
        report = bytes(data).ljust(REPORT_LEN, b"\x00")
        if self.log:
            self.log("write", report)
        # hidapi wants a leading report-ID byte (0 = unnumbered reports).
        if self.dev.write(b"\x00" + report) < 0:
            raise DeviceError("Writing to the oximeter failed")

    def read(self, timeout_ms):
        data = self.dev.read(REPORT_LEN, timeout_ms)
        if not data:
            return None
        data = bytes(data)
        if self.log:
            self.log("read", data)
        return data

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Decoders for the stored-data streams
# --------------------------------------------------------------------------

def decode_auto(packets, count):
    """Decode an Auto-mode stream into `count` values.

    30-byte packets: ED 04 <measurement> <seq lo> <seq hi> <3 flag bytes>
    <21 data bytes> <checksum>. Each data byte holds up to two values as
    differences from a running "top" value; pairs of bytes with flag set and
    high nibble 7 set a new top.
    """
    values = []
    top = 0
    adjust_first = True
    for pkt in packets:
        flags = pkt[5] + (pkt[6] << 7) + (pkt[7] << 14)
        for b in pkt[8:29]:
            if len(values) >= count:
                return values
            flag = flags & 1
            flags >>= 1
            hi, lo = b >> 4, b & 0x0F
            if flag and hi == 0x7:
                if adjust_first:
                    top = 16 * lo
                else:
                    top = (top + lo) & 0xFF
                adjust_first = not adjust_first
                continue
            values.append((top - (hi + 8 if flag else hi)) & 0xFF)
            if lo != 0x0F and len(values) < count:
                values.append((top - lo) & 0xFF)
        if len(values) >= count:
            return values
    return values


def decode_manual(packets, count, artifact):
    """Decode a Manual-mode stream into `count` values.

    20-byte packets: <D3|D2> <seq lo> <seq hi> <2 flag bytes> <14 data bytes>
    <checksum>. The first data byte of a packet is an absolute value; the rest
    hold two signed differences each.
    """
    values = []
    datum = 0

    def put(v):
        if len(values) < count:
            values.append(v)

    for pkt in packets:
        flags = pkt[3] + (pkt[4] << 7)
        for i, b in enumerate(pkt[5:19]):
            if len(values) >= count:
                return values
            flag = flags & 1
            flags >>= 1
            is_artifact = flag and b == 0x7F
            if i == 0:
                datum = b
                if is_artifact:
                    put(artifact)
                else:
                    if flag:  # value above 127
                        datum = (datum + 128) & 0xFF
                    put(datum)
                continue
            if is_artifact:
                put(artifact)
                put(artifact)
                continue
            hi, lo = b >> 4, b & 0x0F
            datum = (datum - hi if flag else datum + hi) & 0xFF
            put(datum)
            if lo == 0x0F:  # finger out
                put(artifact)
                continue
            if lo & 0x8:
                datum = (datum - (lo & 0x7)) & 0xFF
            else:
                datum = (datum + lo) & 0xFF
            put(datum)
        if len(values) >= count:
            return values
    return values


# --------------------------------------------------------------------------
# Device protocol
# --------------------------------------------------------------------------

class Record:
    def __init__(self, index, start, seconds, mode):
        self.index = index      # record number on the watch (Auto mode)
        self.start = start      # naive datetime, watch local time
        self.seconds = seconds  # one sample per second
        self.mode = mode        # "auto" or "manual"

    @property
    def basename(self):
        """File name without extension, from the start time."""
        return "CMS50F_%s" % self.start.strftime("%Y-%m-%d_%H-%M-%S")

    def describe(self):
        h, rem = divmod(self.seconds, 3600)
        return "%s  %d h %02d min  (%s, %d samples)" % (
            self.start.strftime("%d-%b-%y %H:%M:%S"), h, rem // 60,
            self.mode.capitalize(), self.seconds)


def parse_start(b):
    try:
        return dt.datetime(2000 + b[0], b[1], b[2], b[3], b[4], b[5])
    except ValueError:
        raise DeviceError("The watch reported an invalid start time: %s" % hexs(b[:6]))


class CMS50F:
    def __init__(self, transport):
        self.t = transport
        self.model = ""
        self.user = ""

    # -- low level ---------------------------------------------------------

    def send(self, data):
        self.t.write(bytes(data).ljust(REPORT_LEN, b"\x00"))

    def drain(self):
        """Throw away reports left over from an earlier stream."""
        for _ in range(500):
            if self.t.read(DRAIN_TIMEOUT) is None:
                return

    def exchange(self, command, reply_first, reply_len):
        """Send one command and return the reply report.

        Reports that do not start with the expected byte (for example live
        data still arriving) are skipped.
        """
        self.drain()
        self.send(command)
        deadline = time.monotonic() + REPLY_TIMEOUT / 1000
        while time.monotonic() < deadline:
            r = self.t.read(REPLY_TIMEOUT)
            if r is None:
                break
            if r[0] != reply_first:
                continue
            if checksum(r[:reply_len - 1]) != r[reply_len - 1]:
                raise DeviceError("Bad checksum in reply to %s: %s"
                                  % (hexs(command[:3]), hexs(r[:reply_len])))
            return r
        raise DeviceError("No reply from the oximeter to command %s"
                          % hexs(command[:3]))

    def read_packets(self, request, packet_len, first_byte):
        """Send a data request and yield checked packets from the stream.

        Packets are laid back to back across 64-byte reports, so one can be
        split between two reports.
        """
        self.drain()
        self.send(request)
        buf = b""
        seq = 0
        started = False
        while True:
            r = self.t.read(DATA_TIMEOUT)
            if r is None:
                raise DeviceError(
                    "The oximeter stopped sending data after %d packets" % seq)
            if not started:
                if r[0] != first_byte:
                    continue  # stale report from something else
                started = True
            buf += r
            while len(buf) >= packet_len:
                pkt, buf = buf[:packet_len], buf[packet_len:]
                if pkt[0] != first_byte:
                    raise DeviceError("Unexpected data packet: %s" % hexs(pkt))
                if checksum(pkt[:-1]) != pkt[-1]:
                    raise DeviceError("Bad checksum in data packet %d" % seq)
                pseq = pkt[1] + (pkt[2] << 7) if packet_len == 20 else pkt[3] + (pkt[4] << 7)
                if pseq != seq:
                    raise DeviceError("Data packet out of order: got %d, expected %d"
                                      % (pseq, seq))
                seq += 1
                yield pkt

    # -- commands ----------------------------------------------------------

    def connect(self, now=None):
        """Start-up sequence copied from the maker's software.

        It also sets the watch clock to the PC's local time.
        """
        stop = bytes([0x7D, 0x81, 0xA7] + [0x80] * 6 + [0x7D, 0x81, 0xA2] + [0x80] * 6)
        self.exchange(stop, 0xF0, 2)
        self.exchange(bytes([0x82, 0x02]), 0xF2, 8)
        self.exchange(bytes([0x80, 0x00]), 0xF0, 2)
        now = now or dt.datetime.now()
        self.exchange(with_checksum(0x83, now.year - 2000, now.month, now.day,
                                    now.hour, now.minute, now.second, 0, 0), 0xF3, 3)
        r = self.exchange(with_checksum(0x8E, 0x03), 0xFE, 10)
        self.user = bytes(r[2:9]).decode("ascii", "replace").strip(" \x00")
        r = self.exchange(with_checksum(0x81), 0xF1, 10)
        self.model = bytes(r[1:9]).decode("ascii", "replace").strip(" \x00")
        return now

    def list_records(self):
        r = self.exchange(with_checksum(0x9F), 0xEF, 8)
        if not r[2]:
            return []
        if r[3]:
            return self._list_auto()
        return [self._manual_record()]

    def _list_auto(self):
        records = []
        while True:
            r = self.exchange(with_checksum(0x9C, 0x01), 0xEC, 21)
            seconds = r[10] + (r[11] << 7) + (r[12] << 14)
            records.append(Record(r[3], parse_start(r[4:10]), seconds, "auto"))
            if r[1] or len(records) >= MAX_RECORDS:
                return records

    def _manual_record(self):
        r = self.exchange(with_checksum(0xA0, 0x00), 0xD0, 14)
        seconds = r[10] + (r[11] << 7) + (r[12] << 14)
        return Record(1, parse_start(r[2:8]), seconds, "manual")

    def download(self, rec):
        """Return (spo2_values, pulse_values) for one record, oldest first."""
        if rec.seconds == 0:
            return [], []
        if rec.mode == "auto":
            def get(measurement):
                req = with_checksum(0x9D, 0x04, measurement, 0x01, rec.index, 0, 0, 0)
                return decode_auto(self.read_packets(req, 30, 0xED), rec.seconds)
            spo2, pulse = get(AUTO_SPO2), get(AUTO_PULSE)
        else:
            def get(measurement, artifact):
                req = with_checksum(measurement, 0, 0, 0)
                first = 0xD3 if measurement == MANUAL_SPO2 else 0xD2
                return decode_manual(self.read_packets(req, 20, first),
                                     rec.seconds, artifact)
            spo2 = get(MANUAL_SPO2, ARTIFACT_SPO2)
            pulse = get(MANUAL_PULSE, ARTIFACT_PULSE)
        for name, vals in (("SpO2", spo2), ("pulse", pulse)):
            if len(vals) != rec.seconds:
                raise DeviceError("Got %d %s values, expected %d"
                                  % (len(vals), name, rec.seconds))
        return spo2, pulse


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def write_csv(path, rec, spo2, pulse):
    """Write time, SpO2, pulse. Invalid readings (finger out) are left empty."""
    tmp = path + ".part"
    valid = 0
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "spo2", "pulse"])
        for i, (s, p) in enumerate(zip(spo2, pulse)):
            ts = rec.start + dt.timedelta(seconds=i)
            s_ok = 1 <= s <= 100
            p_ok = p not in (0, 255)
            valid += s_ok and p_ok
            w.writerow([ts.strftime("%Y-%m-%d %H:%M:%S"),
                        s if s_ok else "", p if p_ok else ""])
    os.replace(tmp, path)
    return valid


def spo2_header():
    """The fixed 1056-byte header of the .SpO2 files Contec's software writes.

    Byte 0-1: offset of the record block (0x358), little-endian. OSCAR reads
    the date at this offset + 200. The rest is copied from a file the maker's
    software saved (via pulseoxdl's data/header): a few flag bytes and the
    UTF-16 text "BLE", otherwise zeros.
    """
    h = bytearray(1056)
    h[0:2] = struct.pack("<H", 0x358)
    h[4] = 0x01
    h[0x358] = 0x01
    h[0x360:0x366] = "BLE".encode("utf-16-le")
    return bytes(h)


def write_spo2(path, rec, spo2, pulse):
    """Write Contec's binary .SpO2 format, which OSCAR can import.

    Layout: header, then year, month, day, hour, minute, second and the number
    of samples as 32-bit little-endian integers, then one SpO2 byte and one
    pulse byte per second. Raw values are kept: OSCAR treats SpO2 127 with
    pulse 255 as "no reading", like the maker's files.
    """
    t = rec.start
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(spo2_header())
        f.write(struct.pack("<7I", t.year, t.month, t.day, t.hour, t.minute,
                            t.second, len(spo2)))
        f.write(bytes(b for pair in zip(spo2, pulse) for b in pair))
    os.replace(tmp, path)


def default_out_dir():
    base = os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                           else os.path.abspath(__file__))
    return os.path.join(base, "CMS50F")


def run(transport, out_dir, list_only=False, force=False, say=print):
    dev = CMS50F(transport)
    now = dev.connect()
    say("Connected: model %s, user '%s'." % (dev.model or "?", dev.user))
    say("Watch clock set to %s." % now.strftime("%d-%b-%y %H:%M:%S"))
    records = dev.list_records()
    if not records:
        say("No records on the watch.")
        return []
    say("%d record(s) on the watch:" % len(records))
    for rec in records:
        say("  " + rec.describe())
    if list_only:
        say("Listed only (--list): nothing was downloaded.")
        return []

    os.makedirs(out_dir, exist_ok=True)
    saved = []
    for rec in records:
        base = os.path.join(out_dir, rec.basename)
        csv_path, spo2_path = base + ".csv", base + ".SpO2"
        if rec.seconds == 0:
            say("  %s: empty record, skipped." % rec.basename)
            continue
        if os.path.exists(csv_path) and os.path.exists(spo2_path) and not force:
            say("  %s: already downloaded, skipped." % rec.basename)
            continue
        say("  %s: downloading..." % rec.basename)
        spo2, pulse = dev.download(rec)
        valid = write_csv(csv_path, rec, spo2, pulse)
        write_spo2(spo2_path, rec, spo2, pulse)
        pct = 100.0 * valid / rec.seconds
        say("  %s: saved .csv and .SpO2, %d seconds, %.0f%% valid."
            % (rec.basename, rec.seconds, pct))
        saved.append(csv_path)
    return saved


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Download records from a Contec CMS50F/CMS50E USB-C (HID) "
                    "pulse oximeter to CSV and to .SpO2 files that OSCAR can import. "
                    "Records stay on the watch.")
    ap.add_argument("--out", default=default_out_dir(),
                    help="output folder (default: a CMS50F folder next to this script)")
    ap.add_argument("--list", action="store_true",
                    help="only list the records, do not download")
    ap.add_argument("--force", action="store_true",
                    help="download again even if the files already exist")
    ap.add_argument("--debug", action="store_true",
                    help="write every USB report to debug-<time>.log in the output folder")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)

    logf = None
    log = None
    if args.debug:
        os.makedirs(args.out, exist_ok=True)
        logpath = os.path.join(args.out, "debug-%s.log"
                               % dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
        logf = open(logpath, "w")
        print("Debug log: %s" % logpath)

        def log(direction, data):
            logf.write("%s %-5s %s\n" % (dt.datetime.now().strftime("%H:%M:%S.%f")[:-3],
                                         direction, hexs(data)))
            logf.flush()

    transport = None
    code = 0
    try:
        transport = HidTransport(log=log)
        run(transport, args.out, list_only=args.list, force=args.force)
        print("Files are in %s" % os.path.abspath(args.out))
    except DeviceError as e:
        print("Error: %s" % e, file=sys.stderr)
        code = 1
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        code = 1
    finally:
        if transport:
            transport.close()
        if logf:
            logf.close()
    if getattr(sys, "frozen", False):
        input("Press Enter to close.")
    return code


if __name__ == "__main__":
    sys.exit(main())
