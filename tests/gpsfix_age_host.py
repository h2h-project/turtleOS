#!/usr/bin/env python3
# tests/gpsfix_age_host.py — review B5: a GPS fix days old must never read as fresh
#
#   cd device && python3 ../tests/gpsfix_age_host.py      (~1 s)
#
# Fakes time.ticks_ms / ticks_diff with the ESP32 port's arithmetic (period
# 2^30 ms = 12.4 days, ticks_diff signed within half of that), then runs the
# real src/app/monotonic.py and src/nav/gpsfix.py through months of uptime:
#   - the old ticks_diff age goes negative after ~6.2 days (the bug);
#   - monotonic.ms() follows true elapsed time across many wraps;
#   - gpsfix.get() / gga() ages stay correct at 7, 30, 90 days and 3 years;
#   - monotonic.ms() never steps backwards, even if it is starved.
# Exit code 0 = all good.

import os
import sys
import time

sys.path.insert(0, os.getcwd())

PERIOD = 1 << 30
HALF = PERIOD >> 1
TRUE_MS = [0]           # real elapsed ms since "boot"


def ticks_ms():
    return TRUE_MS[0] % PERIOD


def ticks_diff(a, b):
    return ((a - b + HALF) % PERIOD) - HALF


time.ticks_ms = ticks_ms
time.ticks_diff = ticks_diff

from src.app import monotonic  # noqa: E402
from src.nav import gpsfix     # noqa: E402

DAY = 86400 * 1000
FAILS = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def advance(ms, step=1000 * 60):
    """Move true time on by ms, calling monotonic.ms() every `step` ms the
    way _bg_tick does (coarser than reality, still far under 6.2 days)."""
    end = TRUE_MS[0] + ms
    while TRUE_MS[0] < end:
        TRUE_MS[0] = min(end, TRUE_MS[0] + step)
        monotonic.ms()


# 1. Demonstrate the bug the old code had.
old_stamp = ticks_ms()
TRUE_MS[0] += 7 * DAY
check(ticks_diff(ticks_ms(), old_stamp) < 0,
      "old ticks_diff age of a 7-day-old stamp is negative (the B5 bug)")
TRUE_MS[0] = 0

# 2. monotonic.ms() tracks true time across many wraps.
base_true = TRUE_MS[0]
base = monotonic.ms()
advance(100 * DAY, step=10 * 60 * 1000)
check(monotonic.ms() - base == TRUE_MS[0] - base_true,
      "monotonic.ms() exact after 100 days (8 ticks wraps)")

# 3. GPS fix ages.
gpsfix.update(43.1, 5.9, 90.0)
old_fix_tick = ticks_ms()          # what the old gpsfix stored
gpsfix.note_gga("$GNGGA,120000.00,4306.000,N,00554.000,E,1,07,1.0,0.0,M,0.0,M,,*00")
lat, lon, age = gpsfix.get()
check(lat == 43.1 and age == 0, "fresh fix has age 0")
for days in (1, 6, 7, 13, 30, 90, 365 * 3):
    target = days * DAY
    _, _, cur = gpsfix.get()
    advance(target - cur, step=60 * 60 * 1000)
    _, _, age = gpsfix.get()
    _q, _s, gage = gpsfix.gga()
    check(age == target and gage == target,
          "fix age reads {:g} days after {} days (old code read {:+.1f} days)".format(
              age / DAY, days, ticks_diff(ticks_ms(), old_fix_tick) / DAY))
    check(not (age < 300000), "  ...and is not within the 5 min telemetry fallback")

# 4. A new fix resets the age.
gpsfix.update(43.2, 6.0)
check(gpsfix.get()[2] == 0, "a new fix after 3 years reads age 0")

# 5. Starved for longer than half a period: stands still, never goes back.
before = monotonic.ms()
TRUE_MS[0] += HALF + 5 * DAY       # nothing called ms() for ~11 days
after = monotonic.ms()
check(after >= before, "starved clock never steps backwards")

# 6. Values above 2^31 are fine as plain ints.
check(monotonic.ms() > (1 << 31), "monotonic.ms() has grown past 2^31 without wrapping")

print()
if FAILS:
    print("{} FAILED".format(len(FAILS)))
    sys.exit(1)
print("all good")
