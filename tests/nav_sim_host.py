# tests/nav_sim_host.py — HOST-side (CPython) simulation of the nav stack.
#
# Unlike the other scripts in tests/ this one does NOT run on the device:
#   cd device && python3 ../tests/nav_sim_host.py
#
# It shims MicroPython's time.ticks_* onto a controllable fake clock and
# exercises: bearing math, the state machine transition table, a full luff
# sweep against a simulated fluttering sail (with whole-degree servo
# quantization), and a full NavController mission:
# ACQUIRE → sweep → SAIL_NAV (PID steering) → waypoint advance → ARRIVAL,
# plus GPS-loss → SAFE.

import sys
import os
import time
import random
import tempfile

sys.path.insert(0, ".")

CLOCK = [0]
time.ticks_ms = lambda: CLOCK[0]
time.ticks_add = lambda a, b: a + b
time.ticks_diff = lambda a, b: a - b
time.sleep_ms = lambda ms: None


class FakeServo:
    def __init__(self):
        self.cmd = 90.0

    def angle(self, d):
        self.cmd = float(d)

    def center(self):
        self.cmd = 90.0


class FakeEnc:
    """Encoder tracks the (whole-degree quantized) servo; flutters near wind."""
    is_present = True

    def __init__(self, servo, wind_deg):
        self.s = servo
        self.wind = wind_deg

    def angle(self):
        a = float(int(self.s.cmd))
        if self.wind is not None and abs(a - self.wind) < 12:
            a += random.uniform(-2.5, 2.5)      # luff flutter
        else:
            a += random.uniform(-0.05, 0.05)    # calm sensor noise
        return a % 360.0


class FakeHeading:
    def __init__(self, deg=45.0):
        self.h = deg

    def heading_deg(self):
        return self.h

    def is_stable(self):
        return True


def test_bearing():
    from src.nav.bearing import norm180, norm360, initial_bearing, distance_m, midpoint_angle
    assert norm180(350) == -10
    assert norm360(-10) == 350
    assert abs(midpoint_angle(350, 10)) < 1e-9
    assert abs(distance_m(0, 0, 0, 1) - 111195) < 200
    assert abs(initial_bearing(0, 0, 0, 1) - 90.0) < 1e-6
    print("bearing OK")


def test_state_machine():
    from src.nav import state_machine as sm
    sm._state[0] = sm.BOOT
    assert not sm.set_state(sm.SAIL_NAV)        # invalid skip
    assert sm.set_state(sm.ACQUIRE)
    assert sm.set_state(sm.SAFE, "fault")       # any → SAFE
    assert not sm.set_state(sm.SAIL_NAV)        # SAFE locked
    assert sm.set_state(sm.ACQUIRE, "manual reset")
    assert sm.display_name(sm.SAIL_NAV) == "SAIL-NAV"
    print("state machine OK")


def run_sweep(wind, seed):
    from src.nav.luff import LuffSweep
    random.seed(seed)
    servo = FakeServo()
    enc = FakeEnc(servo, wind)
    sw = LuffSweep(servo, enc, speed_dps=8.0, threshold_mult=5.0,
                   sail_min=10, sail_max=170, step_ms=50)
    sw.start(now_ms=0)
    t = 0
    st = None
    for _ in range(20000):
        t += 50
        st = sw.step(now_ms=t)
        if st["phase"] in ("done", "failed"):
            break
    return st


def test_luff_sweep():
    for wind in (50.0, 95.0, 140.0):
        for seed in (1, 7, 42):
            st = run_sweep(wind, seed)
            assert st["done"], (wind, seed, st)
            err = abs(st["wind_angle"] - wind)
            assert err < 8, (wind, seed, st)
    st = run_sweep(None, 1)                     # windless: clean failure
    assert st["phase"] == "failed" and st["error"]
    print("luff sweep OK (9/9 wind solves, windless fails cleanly)")


def test_mission():
    import src.nav.waypoints as wpm
    wpm._STATE_PATH = os.path.join(tempfile.mkdtemp(), "nav_state.json")

    from src.nav import state_machine as sm
    from src.nav import gpsfix
    from src.nav.controller import NavController
    from src.nav.luff import LuffSweep

    random.seed(3)
    cfg = {
        "nav_cycle_ms": 300, "arrival_radius_m": 300, "gps_loss_safe_s": 120,
        "low_batt_pct": 20, "sail_min_deg": 10, "sail_max_deg": 170,
        "luff_sweep_dps": 8, "luff_threshold_mult": 5.0,
        "waypoints": [[31.36, 34.27], [31.40, 34.20]], "compass_offset_deg": 0,
    }
    servo = FakeServo()
    c = NavController(cfg, i2c=None, gps=None, servo=servo, battery=None)
    c._enc = FakeEnc(servo, 95.0)
    c._heading = FakeHeading()
    c._sweep = LuffSweep(servo, c._enc, speed_dps=8, threshold_mult=5.0,
                         sail_min=10, sail_max=170)

    sm._state[0] = sm.BOOT
    assert not c.begin_luff_sweep()             # refused in BOOT
    sm.set_state(sm.ACQUIRE, "boot complete")
    assert c.begin_luff_sweep()

    boat = (31.355, 34.272)
    while sm.get_state() == sm.ACQUIRE and CLOCK[0] < 600000:
        CLOCK[0] += 25
        gpsfix.update(*boat)
        c.tick(cfg)
    assert sm.get_state() == sm.SAIL_NAV, c.sweep_status()
    assert abs(c.wind_angle() - 95.0) < 8

    cmds = set()
    for _ in range(20):
        CLOCK[0] += 300
        gpsfix.update(*boat)
        c.tick(cfg)
        cmds.add(int(servo.cmd))
    assert cmds != {90}, "PID must steer"

    for wp in cfg["waypoints"]:
        for _ in range(2):
            CLOCK[0] += 300
            gpsfix.update(wp[0], wp[1])
            c.tick(cfg)
    assert sm.get_state() == sm.ARRIVAL
    assert servo.cmd == 90.0                    # feathered

    # Fresh mission: clear the persisted waypoint index (otherwise the new
    # controller resumes at "final reached" and correctly enters ARRIVAL)
    # and age out the GPS fix → SAFE.
    os.remove(wpm._STATE_PATH)
    gpsfix._fix[:] = [31.0, 34.0, None, CLOCK[0]]
    sm._state[0] = sm.SAIL_NAV
    c2 = NavController(cfg, i2c=None, gps=None, servo=servo, battery=None)
    c2._heading = FakeHeading()
    t0 = CLOCK[0]
    while sm.get_state() == sm.SAIL_NAV and CLOCK[0] < t0 + 300000:
        CLOCK[0] += 300
        c2.tick(cfg)
    assert sm.get_state() == sm.SAFE
    assert c2.fault == "gps loss"
    print("mission OK (ACQUIRE→sweep→SAIL_NAV→ARRIVAL; GPS loss→SAFE)")


if __name__ == "__main__":
    test_bearing()
    test_state_machine()
    test_luff_sweep()
    test_mission()
    print("ALL NAV SIM TESTS PASSED")
