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
# plus GPS-loss → SAFE, and the journey resume after a reboot (battery →
# GPS fix → wind sweep → SAIL_NAV) with SAFE recovery.

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
        "mission_waypoints": [[31.36, 34.27], [31.40, 34.20]], "compass_offset_deg": 0,
    }
    import src.nav.controller as ctl_mod
    ctl_mod._journey_open = lambda: False       # no journey: old manual path
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

    for wp in cfg["mission_waypoints"]:
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


def test_resume():
    """A journey open at boot: battery first, then a GPS fix, then the wind
    sweep, then SAIL_NAV. Battery drops send it back to stage 1; SAFE clears
    when the fix returns."""
    import src.nav.waypoints as wpm
    wpm._STATE_PATH = os.path.join(tempfile.mkdtemp(), "nav_state.json")
    from src.nav import state_machine as sm
    from src.nav import gpsfix
    import src.nav.controller as ctl_mod
    from src.nav.controller import NavController
    from src.nav.luff import LuffSweep

    random.seed(5)
    cfg = {
        "nav_cycle_ms": 300, "arrival_radius_m": 300, "gps_loss_safe_s": 120,
        "low_batt_pct": 20, "resume_batt_pct": 40, "luff_resweep_s": 600,
        "sail_min_deg": 10, "sail_max_deg": 170,
        "luff_sweep_dps": 8, "luff_threshold_mult": 5.0,
        "mission_waypoints": [[31.40, 34.20]], "compass_offset_deg": 0,
    }
    JOURNEY = [False]
    PCT = [30]
    ctl_mod._journey_open = lambda: JOURNEY[0]

    def make():
        servo = FakeServo()
        c = NavController(cfg, i2c=None, gps=None, servo=servo, battery=None)
        c._enc = FakeEnc(servo, 95.0)
        c._heading = FakeHeading()
        c._sweep = LuffSweep(servo, c._enc, speed_dps=8, threshold_mult=5.0,
                             sail_min=10, sail_max=170)
        c._battery_pct = lambda: PCT[0]
        return c, servo

    boat = (31.355, 34.272)

    def run(c, ms, fix, until=None, step=25):
        end = CLOCK[0] + ms
        while CLOCK[0] < end:
            CLOCK[0] += step
            if fix:
                gpsfix.update(*boat)
            c.tick(cfg)
            if until is not None and until():
                return True
        return until is None

    gpsfix._fix[:] = [None, None, None, 0]
    sm._state[0] = sm.BOOT
    c, servo = make()
    sm.set_state(sm.ACQUIRE, "boot complete")

    # No journey: ACQUIRE idles, no stage.
    run(c, 5000, fix=False)
    assert sm.get_state() == sm.ACQUIRE and c.resume_stage() is None

    # Journey open, battery low: stage 1, feathered, nothing else moves.
    JOURNEY[0] = True
    servo.cmd = 33.0
    run(c, 1000, fix=False)
    assert c.resume_stage() == "BATTERY", c.resume_stage()
    assert sm.resume_stage() == "BATTERY"
    assert servo.cmd == 90.0                    # feathered once
    assert not c.begin_luff_sweep()             # battery first
    assert c.snapshot()["resume"] == "BATTERY"
    run(c, 600000, fix=True, step=300)          # 10 min, fix or not
    assert c.resume_stage() == "BATTERY" and not c.sweeping()
    assert servo.cmd == 90.0

    # Charged past resume_batt_pct (not just low_batt_pct): stage 2.
    PCT[0] = 25
    run(c, 2000, fix=False, step=300)
    assert c.resume_stage() == "BATTERY"        # hysteresis: 25 < 40
    gpsfix._fix[:] = [None, None, None, 0]
    PCT[0] = 45
    run(c, 1000, fix=False, step=300)
    assert c.resume_stage() == "GPS", c.resume_stage()

    # No fix for 10 minutes: still waiting, never SAFE, no sweep.
    run(c, 600000, fix=False, step=300)
    assert sm.get_state() == sm.ACQUIRE and c.resume_stage() == "GPS"
    assert not c.sweeping()

    # Battery dips while waiting for GPS: back to stage 1.
    PCT[0] = 15
    run(c, 1000, fix=False, step=300)
    assert c.resume_stage() == "BATTERY"
    PCT[0] = 45
    run(c, 1000, fix=False, step=300)
    assert c.resume_stage() == "GPS"

    # Fix arrives: wind sweep, then SAIL_NAV with the re-sweep armed.
    assert run(c, 2000, fix=True, until=lambda: c.resume_stage() == "WIND")
    assert c.sweeping()
    assert run(c, 600000, fix=True, until=lambda: sm.get_state() == sm.SAIL_NAV)
    assert c.resume_stage() is None
    assert abs(c.wind_angle() - 95.0) < 8, c.wind_angle()
    nxt = c.seconds_to_next_sweep()
    assert nxt is not None and 590 <= nxt <= 600, nxt     # B10

    cmds = set()
    for _ in range(20):
        CLOCK[0] += 300
        gpsfix.update(*boat)
        c.tick(cfg)
        cmds.add(int(servo.cmd))
    assert cmds != {90}, "PID must steer"

    # Low battery while sailing: feather, ACQUIRE, stage 1 (B9: no chatter).
    PCT[0] = 19
    run(c, 1000, fix=True, step=300)
    assert sm.get_state() == sm.ACQUIRE and c.resume_stage() == "BATTERY"
    assert servo.cmd == 90.0
    PCT[0] = 30
    run(c, 3000, fix=True, step=300)
    assert c.resume_stage() == "BATTERY"        # 30 < 40, stays put
    # Recharged; the wind was solved minutes ago, so no second sweep.
    PCT[0] = 45
    assert run(c, 3000, fix=True, until=lambda: sm.get_state() == sm.SAIL_NAV, step=300)
    assert not c.sweeping()

    # GPS lost at sea: SAFE, then clears once the fix is back (B4).
    t0 = CLOCK[0]
    assert run(c, 300000, fix=False, until=lambda: sm.get_state() == sm.SAFE, step=300)
    assert c.fault == "gps loss" and CLOCK[0] - t0 >= 120000
    run(c, 60000, fix=False, step=300)
    assert sm.get_state() == sm.SAFE            # no fix, stays SAFE
    assert run(c, 60000, fix=True, until=lambda: sm.get_state() == sm.SAIL_NAV, step=300)
    assert c.fault is None

    # Journey ends (actions._stand_down): ACQUIRE, no stage, stays there.
    sm.set_state(sm.ACQUIRE, "journey ended")
    JOURNEY[0] = False
    run(c, 5000, fix=True, step=300)
    assert sm.get_state() == sm.ACQUIRE and c.resume_stage() is None

    # Journey started from ARRIVAL goes through ACQUIRE and the stages.
    sm._state[0] = sm.ARRIVAL
    assert sm.set_state(sm.ACQUIRE, "journey started")
    JOURNEY[0] = True
    assert run(c, 600000, fix=True, until=lambda: sm.get_state() == sm.SAIL_NAV, step=50)

    # Reboot mid-journey with a wind angle long gone: a fresh controller
    # sweeps again before steering.
    gpsfix._fix[:] = [None, None, None, 0]
    sm._state[0] = sm.BOOT
    c, servo = make()
    sm.set_state(sm.ACQUIRE, "boot complete")
    run(c, 1000, fix=False, step=300)
    assert c.resume_stage() == "GPS"            # battery 45% passes at once
    assert run(c, 2000, fix=True, until=lambda: c.resume_stage() == "WIND")
    assert run(c, 600000, fix=True, until=lambda: sm.get_state() == sm.SAIL_NAV)

    # No battery monitor: stage 1 passes. Autopilot off: nothing moves.
    sm._state[0] = sm.BOOT
    c, servo = make()
    c._battery_pct = lambda: None
    cfg_off = dict(cfg, autopilot_sail=False)
    sm.set_state(sm.ACQUIRE, "boot complete")
    servo.cmd = 33.0
    for _ in range(100):
        CLOCK[0] += 300
        gpsfix.update(*boat)
        c.tick(cfg_off)
    assert servo.cmd == 33.0 and sm.get_state() == sm.ACQUIRE
    run(c, 1000, fix=True, step=300)
    assert c.resume_stage() in ("WIND", None)

    sm._resume[0] = None
    print("resume OK (battery → GPS → wind → SAIL_NAV; low battery and SAFE recover)")


if __name__ == "__main__":
    test_bearing()
    test_state_machine()
    test_luff_sweep()
    test_mission()
    test_resume()
    print("ALL NAV SIM TESTS PASSED")
