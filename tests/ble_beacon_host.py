#!/usr/bin/env python3
# tests/ble_beacon_host.py — the wrangle window, the beacon and BLE_POWER_TEST
#
#   python3 tests/ble_beacon_host.py           (from the repo root; a few s)
#
# Runs the real src/net/ble_service.py, ble_power_test.py and the two
# ble_commands.py handlers with the radio, INA219, button and clock faked.
# Checks (docs/wrangler/README.md, "Wrangle window" and BLE_POWER_TEST):
#   - window open: advertising every 0.5 s; closed: the beacon at
#     ble_beacon_ms; no bonds stored, or ble_beacon_ms 0: no advertising;
#   - after a disconnect the beacon resumes;
#   - a beacon link not bonded after 15 s is dropped, a bonded one is kept,
#     and so is any link on a ble_require_bond=false bench turtle or one
#     made while the window was open;
#   - the power test runs every block, pools rounds, drops and flags a
#     phone that connects mid-test, stops on a click, restores the INA219,
#     leaves the radio on with a fresh window, and BLE_POWER_RESULT packs
#     the contract's layout.
# Exit code 0 = all good.

import os
import random
import shutil
import struct
import sys
import tempfile
import time as _t
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVICE = os.path.join(REPO, "device")

# ---- MicroPython shims ----
clock = {"ms": 1000}
_t.ticks_ms = lambda: clock["ms"]
_t.ticks_diff = lambda a, b: a - b
_t.ticks_add = lambda a, b: a + b
_t.sleep_ms = lambda ms: clock.__setitem__("ms", clock["ms"] + ms)


def advance(s):
    clock["ms"] += int(s * 1000)


work = tempfile.mkdtemp(prefix="ble_beacon_")
shutil.copytree(os.path.join(DEVICE, "src"), os.path.join(work, "src"))
os.chdir(work)
sys.path.insert(0, work)


class UUID:
    def __init__(self, v):
        self.v = v

    def __bytes__(self):
        return bytes(16)


class FakeBLE:
    def __init__(self):
        self.adv = []             # every gap_advertise interval (None = stop)
        self.disconnects = 0
        self.svc = None           # set by the test: who gets the IRQs

    def irq(self, fn):
        pass

    def config(self, **kw):
        pass

    def active(self, on=None):
        return True

    def gatts_register_services(self, svcs):
        return tuple(tuple(range(100 * i + 1, 100 * i + 1 + len(chars)))
                     for i, (_u, chars) in enumerate(svcs))

    def gatts_set_buffer(self, *a):
        pass

    def gatts_write(self, *a):
        pass

    def gatts_notify(self, *a):
        pass

    def gap_advertise(self, interval, adv_data=None, resp_data=None, connectable=True):
        self.adv.append(interval)

    def gap_disconnect(self, conn):
        self.disconnects += 1
        self.svc._irq(2, (conn, 0, b""))


RADIO = FakeBLE()
bt = types.ModuleType("bluetooth")
bt.BLE = lambda: RADIO
bt.UUID = UUID
sys.modules["bluetooth"] = bt

import src.net.ble_service as S       # noqa: E402
import src.net.ble_power_test as P    # noqa: E402
import src.net.ble_commands as C      # noqa: E402

problems = []


def check(ok, what):
    if not ok:
        problems.append(what)
        print("FAIL:", what, file=sys.stderr)


def last_adv():
    return RADIO.adv[-1] if RADIO.adv else "never"


def connect(svc, conn=7):
    svc._irq(1, (conn, 0, b"\x00" * 6))


def bond(svc, conn=7):
    svc._irq(28, (conn, 1, 1, 1, 16))


def drop(svc, conn=7):
    svc._irq(2, (conn, 0, b""))


def beacon_tests():
    S._secrets.clear()
    S._secrets[(2, b"phone")] = b"ltk"
    S._secrets_loaded = True
    svc = S.BleService({"ble_window_min": 1, "ble_beacon_ms": 2000, "device_name": "Applemore"})
    RADIO.svc = svc
    svc.start()
    svc.tick()
    check(last_adv() == 500_000, "window open: advertise every 0.5 s (got %r)" % last_adv())
    check(not svc.beacon(), "window open is not the beacon")

    advance(61)
    svc.tick()
    check(last_adv() == 2_000_000, "window closed: beacon every 2 s (got %r)" % last_adv())
    check(svc.beacon(), "beacon() true once the window closed")

    S._secrets.clear()
    svc.tick()
    check(last_adv() is None, "no bonds stored: no beacon (got %r)" % last_adv())
    S._secrets[(2, b"phone")] = b"ltk"
    svc.tick()
    check(last_adv() == 2_000_000, "bond back: beacon resumes")

    # A stranger through the beacon: dropped after the grace period.
    connect(svc)
    svc.tick()
    advance(14)
    svc.tick()
    check(RADIO.disconnects == 0, "unbonded beacon link kept during the 15 s grace")
    advance(2)
    svc.tick()
    check(RADIO.disconnects == 1, "unbonded beacon link dropped after 15 s")
    svc.tick()
    check(last_adv() == 2_000_000, "beacon resumes after the drop (got %r)" % last_adv())

    # A bonded phone through the beacon: kept, and the beacon resumes after.
    connect(svc)
    advance(2)
    bond(svc)
    advance(30)
    svc.tick()
    check(RADIO.disconnects == 1, "bonded beacon link kept")
    drop(svc)
    svc.tick()
    check(last_adv() == 2_000_000, "beacon resumes after a bonded phone drops")

    # Bench turtle: unbonded beacon links are kept.
    svc._cfg0["ble_require_bond"] = False
    connect(svc)
    advance(30)
    svc.tick()
    check(RADIO.disconnects == 1, "ble_require_bond false: beacon link kept")
    drop(svc)
    svc._cfg0["ble_require_bond"] = True
    svc.tick()

    # A link made while the window was open outlives it, bonded or not.
    svc.open_window()
    svc.tick()
    check(last_adv() == 500_000, "reopened window: fast again (got %r)" % last_adv())
    connect(svc)
    advance(120)
    svc.tick()
    check(RADIO.disconnects == 1, "window link never dropped for want of a bond")
    drop(svc)
    svc.tick()
    check(last_adv() == 2_000_000, "after a window link drops with the window shut: beacon")

    # ble_beacon_ms 0 is the pre-2.6.4 behaviour.
    svc._beacon_ms = 0
    svc.tick()
    check(last_adv() is None, "ble_beacon_ms 0: advertising stops (got %r)" % last_adv())
    svc._beacon_ms = 2000
    svc.tick()
    return svc


class FakeINA:
    """Current that depends on what the radio is doing, plus noise."""

    def __init__(self, svc, rng):
        self.svc = svc
        self.rng = rng
        self.is_present = True
        self.avg = None
        self.woke = 0
        self.intrude_at = None    # clock ms: a phone connects (once)

    def set_averaging(self, n):
        self.avg = n
        return True

    def wake(self):
        self.woke += 1
        self.avg = 1
        return True

    def true_ma(self):
        if not self.svc.active():
            return 30.0
        if self.svc._adv_us:
            return 40.0 + 600.0 / (self.svc._adv_us / 1000)
        return 40.0

    def current_ma(self):
        if self.intrude_at is not None and clock["ms"] >= self.intrude_at and self.svc.advertising():
            self.intrude_at = None
            connect(self.svc, 9)
        return self.true_ma() + self.rng.gauss(0, 0.5)

    def load_voltage_v(self):
        return 3.9


class FakeBtn:
    def __init__(self, click_at=None):
        self.click_at = click_at

    def reset(self):
        pass

    def poll_action(self):
        if self.click_at is not None and clock["ms"] >= self.click_at:
            self.click_at = None
            return "single"
        return None


def power_tests(svc):
    P.RESULT_FILE = os.path.join(work, "ble_power_test.json")
    rng = random.Random(3)
    ina = FakeINA(svc, rng)

    connect(svc)                           # the phone that asked
    bond(svc)
    kicks = RADIO.disconnects
    t0 = clock["ms"]
    # Intrude in round 1's third block (advertising every 500 ms).
    ina.intrude_at = t0 + 2 * 20_000 + 8_000
    res = P.run(svc, ina, FakeBtn(), None, 20, 2)
    took = (clock["ms"] - t0) / 1000
    check(RADIO.disconnects >= kicks + 2, "asking phone and intruder both dropped")
    check(ina.avg == 1 and ina.woke >= 1, "INA219 back to single conversions")
    check(not res["aborted"], "full run not aborted")
    check(abs(took - 2 * 7 * 20) < 10, "took %.0f s for 2 x 7 x 20 s" % took)
    rows = res["rows"]
    check(len(rows) == 7, "seven rows")
    want = [30.0, 40.0, 41.2, 40.6, 40.3, 40.12, 40.0 + 600 / 10240]
    for r, w in zip(rows, want):
        mean = r["mean_ua"] / 1000
        check(abs(mean - w) < 0.15, "mode %d/%d ms mean %.3f mA, expected ~%.3f"
              % (r["mode"], r["interval_ms"], mean, w))
        check(380 < r["samples"] < 520, "samples pooled over both rounds (%d)" % r["samples"])
        check(r["sem_ua"] < 100, "standard error small (%d uA)" % r["sem_ua"])
        check(r["mean_mv"] == 3900, "battery mV (%r)" % r["mean_mv"])
    check([r["disturbed"] for r in rows] == [False, False, True, False, False, False, False],
          "only the intruded block is flagged: %r" % [r["disturbed"] for r in rows])
    check(svc.active() and svc.window_open() and last_adv() == 500_000,
          "radio on, fresh window, fast advertising after the test")

    # BLE_POWER_RESULT
    svc._mtu = 185
    code, out, _f = C._op_ble_power_result({"svc": svc, "src": {}}, b"")
    check(code == C.OK and len(out) == 8 + 7 * P.ROW_SIZE, "result: OK, 106 bytes (%d)" % len(out))
    fin, block_s, rounds, n, flags = struct.unpack_from(P.HEADER, out, 0)
    check((block_s, rounds, n, flags) == (20, 2, 7, 0), "result header %r" % ((block_s, rounds, n, flags),))
    row2 = struct.unpack_from(P.ROW, out, 8 + 2 * P.ROW_SIZE)
    check(row2[0] == 2 and row2[1] == 500 and row2[6] == 1, "row 2 = adv 500 ms, flagged: %r" % (row2,))
    check(struct.calcsize(P.ROW) == P.ROW_SIZE, "row size matches the contract")
    svc._mtu = 23
    code, out, _f = C._op_ble_power_result({"svc": svc, "src": {}}, b"")
    check(code == C.ERR_WRONG_STATE and out == b"\x01", "MTU 23: WRONG_STATE 1")

    # BLE_POWER_TEST handler
    started = []
    src = {"ina": lambda: ina, "ble_power_test": lambda b, r: started.append((b, r))}
    code, out, follow = C._op_ble_power_test({"svc": svc, "src": src}, bytes([60, 3]))
    check(code == C.OK and struct.unpack("<H", out)[0] == P.duration_s(60, 3), "test: OK <H> seconds")
    follow[2]()
    check(started == [(60, 3)], "test: start called with block_s, rounds")
    for bad in (bytes([19, 1]), bytes([20, 0]), bytes([20, 11])):
        check(C._op_ble_power_test({"svc": svc, "src": src}, bad)[0] == C.ERR_BAD_VALUE,
              "test: BAD_VALUE for %r" % bad)
    check(C._op_ble_power_test({"svc": svc, "src": {}}, bytes([20, 1]))[0] == C.ERR_NO_HARDWARE,
          "test: NO_HARDWARE without an INA219")

    # A click stops it early; the radio still comes back.
    svc.test_radio(-1)
    res = P.run(svc, FakeINA(svc, rng), FakeBtn(click_at=clock["ms"] + 50_000), None, 20, 3)
    check(res["aborted"], "click: aborted")
    check(svc.active() and svc.window_open() and last_adv() == 500_000, "click: radio restored")


def main():
    import io
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        svc = beacon_tests()
    with contextlib.redirect_stdout(io.StringIO()):
        power_tests(svc)
    shutil.rmtree(work, ignore_errors=True)
    print("ble_beacon_host: %s" % ("all good" if not problems else "%d problem(s)" % len(problems)))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
