#!/usr/bin/env python3
# tests/gps_integrity_host.py — review B8: damaged NMEA must never become a fix
#
#   cd device && python3 ../tests/gps_integrity_host.py      (<1 s)
#
# Runs the real src/sensors/xiao_gnss.py (on a fake UART) and src/nav/gpsfix.py:
#   - checksum_ok() accepts real L76K sentences and rejects a changed digit,
#     a missing or short *hh, and garbage;
#   - read_nmea() drops bad-checksum lines (counting them) and still returns
#     the good ones around them, including after its buffer truncates;
#   - gpsfix.update() drops a >1 deg jump, keeps the old fix, relocates only
#     after RELOCATE_AFTER agreeing far fixes, and isn't fooled by scattered
#     glitches that disagree with each other.
# Exit code 0 = all good.

import os
import sys
import time
import types

sys.path.insert(0, os.getcwd())

if not hasattr(time, "ticks_ms"):
    time.ticks_ms = lambda: int(time.monotonic() * 1000)
    time.ticks_diff = lambda a, b: a - b
    time.sleep_ms = lambda ms: None


class FakeUART:
    def __init__(self, *a, **k):
        self.data = b""

    def any(self):
        return len(self.data)

    def read(self, n):
        out, self.data = self.data[:n], self.data[n:]
        return out


machine = types.ModuleType("machine")
machine.UART = FakeUART
machine.Pin = lambda n, *a, **k: n
machine.I2C = object
sys.modules["machine"] = machine

from src.nav import gpsfix          # noqa: E402
from src.sensors import xiao_gnss   # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def nmea(body):
    ck = 0
    for c in body:
        ck ^= ord(c)
    return "$%s*%02X" % (body, ck)


RMC = nmea("GNRMC,120000.000,A,3121.0000,N,03416.2000,E,0.10,45.0,201126,,,A")
GGA = nmea("GNGGA,120000.000,3121.0000,N,03416.2000,E,1,08,1.0,10.0,M,0.0,M,,")
# One digit changed in the latitude (31 deg -> 37 deg), checksum left as was.
BAD = RMC.replace("3121.0000", "3721.0000")

print("checksum_ok")
check(gpsfix.checksum_ok(RMC), "good RMC accepted")
check(gpsfix.checksum_ok(GGA), "good GGA accepted")
check(not gpsfix.checksum_ok(BAD), "changed digit rejected")
check(not gpsfix.checksum_ok(RMC.split("*")[0]), "no checksum rejected")
check(not gpsfix.checksum_ok(RMC[:-1]), "one hex digit rejected")
check(not gpsfix.checksum_ok("$GN*ZZ"), "non-hex rejected")
check(not gpsfix.checksum_ok(""), "empty rejected")

print("read_nmea")
g = xiao_gnss.GnssModule(uart_id=1, tx_pin=43, rx_pin=44)
g.uart.data = (RMC + "\r\n" + BAD + "\r\n" + GGA + "\r\n").encode()
got = []
while True:
    line = g.read_nmea()
    if line is None:
        break
    got.append(line)
check(got == [RMC, GGA], "bad line dropped, good ones returned in order")
check(g.bad_checksums == 1, "bad line counted")

g.uart.data = (("x" * 1500) + "\r\n" + RMC + "\r\n" + GGA + "\r\n").encode()
g._rxbuf = b"junk" * 200
got = []
while True:
    line = g.read_nmea()
    if line is None:
        break
    got.append(line)
check(got == [RMC, GGA], "good lines survive buffer truncation")

print("jump filter")
gpsfix.reset()
check(gpsfix.update(31.35, 34.27) is True, "first fix accepted")
check(gpsfix.update(31.36, 34.28) is True, "small move accepted")
check(gpsfix.update(37.35, 34.27) is False, "1-off 6 deg jump dropped")
check(gpsfix.get()[0] == 31.36, "old fix kept")
check(gpsfix.update(31.37, 34.28) is True, "next good fix accepted")

gpsfix.reset()
gpsfix.update(31.35, 34.27)
for i in range(20):
    far = (40.0 + (i % 2) * 5, 10.0)        # glitches that disagree
    gpsfix.update(*far)
check(gpsfix.get()[0] == 31.35, "scattered glitches never relocate")

gpsfix.reset()
gpsfix.update(31.35, 34.27)
res = [gpsfix.update(51.5, -0.12) for _ in range(gpsfix.RELOCATE_AFTER)]
check(res[:-1] == [False] * (gpsfix.RELOCATE_AFTER - 1) and res[-1] is True,
      "relocates on fix %d at a consistent new place" % gpsfix.RELOCATE_AFTER)
check(gpsfix.get()[0] == 51.5, "cache holds the new place")

gpsfix.reset()
gpsfix.update(0.5, 179.9)
check(gpsfix.update(0.5, -179.9) is True, "crossing the antimeridian is not a jump")

print("\n%s" % ("ALL OK" if not FAILS else "%d FAILED" % len(FAILS)))
sys.exit(1 if FAILS else 0)
