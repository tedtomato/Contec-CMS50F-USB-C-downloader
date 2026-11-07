#!/usr/bin/env python3
"""Replay real CMS50E/F USB traffic through cms50f_download.py.

Uses the recordings in pulseoxdl's test folder: the reports the device sent
while the maker's software downloaded one Auto record and one Manual record,
plus the CSV files the maker's software produced from them. The script under
test talks to a fake device that answers with those reports, and its CSV
output is compared row by row with the maker's CSV.

The fake device also fails the test if the script sends any command it does
not know, such as a delete.

Usage:
    git clone https://codeberg.org/klimd/pulseoxdl.git
    python tests/replay_hid.py path/to/pulseoxdl
"""

import csv
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import cms50f_download as m  # noqa: E402


def reports(path):
    data = open(path, "rb").read()
    return [data[i:i + 64] for i in range(0, len(data), 64)]


def reply(*data):
    """A reply report whose last byte is the checksum of the others.

    pulseoxdl's simulator also recomputes reply checksums before sending.
    """
    return m.with_checksum(*data)


class FakeDevice:
    def __init__(self, testdir, mode):
        self.mode = mode
        tr = os.path.join(testdir, "transmission", mode)
        self.streams = {"spo2": reports(os.path.join(tr, "spo2")),
                        "pr": reports(os.path.join(tr, "pr"))}
        self.queue = []
        self.writes = []

    def write(self, data):
        data = bytes(data)
        assert len(data) == 64, "report must be 64 bytes"
        self.writes.append(data)
        c = data[0]
        if c == 0x7D:
            self.queue.append(reply(0xF0))
            return
        # Every other command ends in a checksum; find its length.
        lengths = {0x82: 2, 0x80: 2, 0x83: 10, 0x8E: 3, 0x81: 2, 0x9F: 2,
                   0x9C: 3, 0xA0: 3, 0x9D: 9, 0xA3: 5, 0xA2: 5}
        if c not in lengths:
            raise AssertionError("unexpected command sent: %s" % m.hexs(data[:9]))
        n = lengths[c]
        assert m.checksum(data[:n - 1]) == data[n - 1], "bad checksum: %s" % m.hexs(data[:n])
        if c == 0x82:
            self.queue.append(reply(0xF2, 0x00, 0x00, 0x02, 0x02, 0x00, 0x0B))
        elif c == 0x80:
            self.queue.append(reply(0xF0))
        elif c == 0x83:
            self.queue.append(reply(0xF3, 0x00))
        elif c == 0x8E:
            self.queue.append(reply(0xFE, 0x03, *b"   user"))
        elif c == 0x81:
            self.queue.append(reply(0xF1, *b"50E     "))
        elif c == 0x9F:
            auto = 0x40 if self.mode == "auto" else 0x00
            self.queue.append(reply(0xEF, 0x00, 0x01, auto, 0, 0, 0))
        elif c == 0x9C:
            # Same as pulseoxdl's recording: last record, index 1,
            # start 15-May-21 12:06:42, 10425 seconds, user "user".
            self.queue.append(reply(0xEC, 0x40, 0x01, 0x01, 0x15, 0x05, 0x0F, 0x0C,
                                    0x06, 0x2A, 0x39, 0x51, 0x00, 0x00, 0x75, 0x73,
                                    0x65, 0x72, 0x00, 0x00))
        elif c == 0xA0:
            # Start 5-Jul-25 16:59:23, 2050 seconds.
            self.queue.append(reply(0xD0, 0x00, 0x19, 0x07, 0x05, 0x10, 0x3B, 0x17,
                                    0x00, 0x00, 0x02, 0x10, 0x00))
        elif c == 0x9D:
            assert data[1] == 0x04 and data[3] == 0x01 and data[4] == 1, m.hexs(data[:9])
            self.queue += self.streams["spo2" if data[2] == 0x01 else "pr"]
        elif c in (0xA3, 0xA2):
            self.queue += self.streams["spo2" if c == 0xA3 else "pr"]

    def read(self, timeout_ms):
        return self.queue.pop(0) if self.queue else None

    def close(self):
        pass


def expected_rows(path):
    """The maker's CSV, with finger-out values blanked like our output."""
    rows = []
    with open(path, newline="") as f:
        r = csv.reader(f)
        next(r)
        for date, tm, spo2, pulse in r:
            s, p = int(spo2), int(pulse)
            rows.append(["%s %s" % (date.strip(), tm.strip()),
                         str(s) if 1 <= s <= 100 else "",
                         str(p) if p not in (0, 255) else ""])
    return rows


def check(testdir, mode):
    out = tempfile.mkdtemp(prefix="cms50f-test-")
    lines = []
    dev = FakeDevice(testdir, mode)
    saved = m.run(dev, out, say=lines.append)
    assert len(saved) == 1, "\n".join(lines)
    with open(saved[0], newline="") as f:
        got = list(csv.reader(f))
    assert got[0] == ["time", "spo2", "pulse"]
    got = got[1:]
    want = expected_rows(os.path.join(testdir, "manufacturer", mode, "csv"))
    mismatches = sum(1 for a, b in zip(got, want) if a != b) + abs(len(got) - len(want))
    print("%-6s %s: %d rows, %d mismatches" % (mode, os.path.basename(saved[0]),
                                                len(got), mismatches))
    for a, b in [(a, b) for a, b in zip(got, want) if a != b][:5]:
        print("   got %s  want %s" % (a, b))

    # A second run must skip the file that is already there.
    lines = []
    assert m.run(FakeDevice(testdir, mode), out, say=lines.append) == []
    assert any("already downloaded" in s for s in lines), lines
    return mismatches


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    testdir = os.path.join(sys.argv[1], "data", "test")
    bad = check(testdir, "auto") + check(testdir, "manual")
    print("PASS" if bad == 0 else "FAIL")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
