#!/usr/bin/env python3
# tests/wind_finder_host.py — the bench sweep's settings on the host
#
#   python3 tests/wind_finder_host.py          (from the repo root; < 1 s)
#
# Runs the real src/ui/screens/wind_finder.py against a fake servo whose
# AS5600 angle flutters near one pulse (the luff), with the clock faked.
# Checks SERVO_BENCH_SWEEP's settings (docs/wrangler/README.md):
#   - no settings: the OLED sweep's 50 us steps and 16-sample window;
#   - a step in degrees of travel gives the right number of positions,
#     ending exactly on max_us;
#   - a hold sets the time at each position, and idle_cb (the BLE pump)
#     runs while the servo holds still, never mid-move;
#   - the luff is found at the fluttering position either way.
# Exit code 0 = all good.

import io
import contextlib
import os
import random
import sys
import time as _t

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "device"))

clock = {"ms": 0}
_t.ticks_ms = lambda: clock["ms"]
_t.ticks_diff = lambda a, b: a - b
_t.ticks_add = lambda a, b: a + b
_t.sleep_ms = lambda ms: clock.__setitem__("ms", clock["ms"] + ms)

from src.ui.screens.wind_finder import WindFinder   # noqa: E402

problems = []


def check(ok, what):
    if not ok:
        problems.append(what)
        print("FAIL:", what, file=sys.stderr)


MIN_US, MAX_US = 500, 2500
LUFF_US = 1700


class Servo:
    def __init__(self, rng):
        self.rng = rng
        self.pulse = None
        self.pulses = []
        self.moved_at = 0

    def write(self, us):
        self.pulse = us
        self.pulses.append(us)
        self.moved_at = clock["ms"]

    def angle(self):
        # Sail angle follows the pulse; it flutters near the luff.
        base = (self.pulse - MIN_US) / (MAX_US - MIN_US) * 270.0
        amp = 4.0 if abs(self.pulse - LUFF_US) <= 60 else 0.2
        return (base + self.rng.uniform(-amp, amp)) % 360.0


def sweep(**kw):
    rng = random.Random(5)
    s = Servo(rng)
    idles = []
    f = WindFinder(s.write, s.angle, min_us=MIN_US, max_us=MAX_US,
                   idle_cb=lambda: idles.append((clock["ms"], clock["ms"] - s.moved_at)), **kw)
    t0 = clock["ms"]
    with contextlib.redirect_stdout(io.StringIO()):
        r = f.run()
    return f, s, r, idles, clock["ms"] - t0


# OLED defaults
f, s, r, idles, took = sweep()
check(f.positions() == 41, "default: 41 positions of 50 us (%d)" % f.positions())
check(s.pulses[-1] == MAX_US, "default: ends on max_us")
check(r["ok"] and abs(r["wind_angle_deg"] - (LUFF_US - MIN_US) / 2000 * 270) < 10,
      "default: luff found (%r)" % r.get("wind_angle_deg"))

# 10 degrees of travel on the 0..180 scale, 3 s hold
step_us = 10 * (MAX_US - MIN_US) / 180.0
f, s, r, idles, took = sweep(step_us=step_us, hold_ms=3000)
check(f.positions() == 19, "10 deg: 19 positions (%d)" % f.positions())
moves = s.pulses[1:]                     # the first write is the move to the start
check(moves[0] == MIN_US and moves[-1] == MAX_US, "10 deg: min to max (%r..%r)" % (moves[0], moves[-1]))
check(all(b > a for a, b in zip(moves, moves[1:])), "10 deg: pulses rise")
per = (took - 900) / f.positions()
check(2900 <= per <= 3100, "3 s hold: %.0f ms per position" % per)
check(len(idles) >= f.positions() * 5, "idle_cb every ~0.5 s while holding (%d calls)" % len(idles))
check(all(since >= 150 for _t2, since in idles), "idle_cb never mid-move")
check(r["ok"] and abs(r["wind_angle_deg"] - (LUFF_US - MIN_US) / 2000 * 270) < 15,
      "10 deg: luff found (%r)" % r.get("wind_angle_deg"))

# 1 degree, 1 s hold
f, s, r, idles, took = sweep(step_us=1 * (MAX_US - MIN_US) / 180.0, hold_ms=1000)
check(f.positions() == 181, "1 deg: 181 positions (%d)" % f.positions())
check(s.pulses[-1] == MAX_US, "1 deg: ends on max_us")

print("wind_finder_host: %s" % ("all good" if not problems else "%d problem(s)" % len(problems)))
sys.exit(1 if problems else 0)
