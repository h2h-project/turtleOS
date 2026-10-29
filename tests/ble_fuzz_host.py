#!/usr/bin/env python3
# tests/ble_fuzz_host.py — fuzz the Turtle Wrangler command channel on the host
#
#   python3 tests/ble_fuzz_host.py            (from the repo root; ~5 s)
#   python3 tests/ble_fuzz_host.py 20000 7    (iterations, random seed)
#
# Runs the real src/net/ble_commands.py CommandRunner with the real
# src/app/actions.py and config.py (in a scratch directory), with the
# hardware faked. Every write is random: known and unknown opcodes, empty,
# short, exact and over-long payloads, random bytes. For each one it checks
# the GATT contract v1 guarantees:
#   - the runner never raises;
#   - exactly one FINAL result per accepted write, echoing opcode and seq
#     (IN_PROGRESS may come first; ERR_BUSY may answer a write that arrives
#     while another command runs);
#   - nothing stays busy forever (polls end, timeouts fire).
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

ITER = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 1

# ---- MicroPython shims ----
clock = {"ms": 1000}
_t.ticks_ms = lambda: clock["ms"]
_t.ticks_diff = lambda a, b: a - b
_t.ticks_add = lambda a, b: a + b
_t.sleep_ms = lambda ms: clock.__setitem__("ms", clock["ms"] + ms)

work = tempfile.mkdtemp(prefix="ble_fuzz_")
shutil.copytree(os.path.join(DEVICE, "src"), os.path.join(work, "src"))
shutil.copy(os.path.join(DEVICE, "config.py"), work)
os.chdir(work)
sys.path.insert(0, work)

side_effects = {"reset": 0, "sleep": 0, "power": 0}
mach = types.ModuleType("machine")
mach.reset = lambda: side_effects.__setitem__("reset", side_effects["reset"] + 1)
mach.RTC = lambda: types.SimpleNamespace(datetime=lambda: (2026, 10, 2, 4, 9, 0, 0, 0))
sys.modules["machine"] = mach

dc = types.ModuleType("src.net.device_client")
dc.patch_set_fields = lambda cfg, fields: None
sys.modules["src.net.device_client"] = dc

import src.app   # noqa: E402
import src.nav   # noqa: E402

jr = {"rec": {}}
journey = types.ModuleType("src.app.journey")
journey.active = lambda: bool(jr["rec"])
journey.active_id = lambda: jr["rec"].get("id")
journey.start = lambda la, lo: jr.__setitem__("rec", {"id": 1790930000}) or jr["rec"]
journey.end = lambda arrival=None: jr.__setitem__("rec", {})
journey.get = lambda: jr["rec"]
journey.history = lambda: [{"id": 1790920000, "ended_at": 1790923600, "departure": [50.85, -1.41],
                            "destination": [50.86, -1.40], "destination_source": 2, "arrival": None}]
journey.last = lambda: journey.history()[0]
journey.TRACK_POINT_SIZE = 13
journey.track_count = lambda jid: 30
journey.track_page = lambda jid, start, n: bytes(13 * max(0, min(n, 30 - start)))
sys.modules["src.app.journey"] = journey
src.app.journey = journey

state = {"s": "ACQUIRE"}
sm = types.ModuleType("src.nav.state_machine")
sm.BOOT, sm.ACQUIRE, sm.SAIL_NAV = "BOOT", "ACQUIRE", "SAIL_NAV"
sm.get_state = lambda: state["s"]
sm.set_state = lambda s, why: state.__setitem__("s", s)
sys.modules["src.nav.state_machine"] = sm
src.nav.state_machine = sm

import config                                   # noqa: E402
from src.nav import gpsfix                     # noqa: E402
from src.net.ble_commands import CommandRunner, OPCODES, IN_PROGRESS, ERR_BUSY  # noqa: E402

config.save_config({"servo_present": True, "wifi_enabled": True,
                    "mission_destination": [31.32919, 34.23108]})


class Nav:
    def __init__(self):
        self.sw = False

    def sweeping(self):
        if self.sw and random.random() < 0.3:
            self.sw = False
        return self.sw

    def begin_luff_sweep(self):
        self.sw = state["s"] in ("ACQUIRE", "SAIL_NAV")
        return self.sw

    def wind_angle(self):
        return random.choice([None, 201.5])

    def heading_source(self):
        return types.SimpleNamespace(set_offset=lambda d: None)


class Tel:
    def __init__(self):
        self.flag = False
        self.api_state = {"ok": None, "sending": False, "last_ms": 1}

    def send_manual(self):
        self.flag = True
        return True

    def tick(self, cfg):
        if random.random() < 0.7:
            self.flag = False
        return random.choice([None, False])

    def manual_pending(self):
        return self.flag

    def clear_manual(self):
        self.flag = False

    def request_now(self):
        if random.random() < 0.5:
            self.api_state.update(ok=random.choice([True, False]), last_ms=clock["ms"])


class WLAN:
    def ifconfig(self):
        return (random.choice(["0.0.0.0", "192.168.1.9"]),)


class Wifi:
    wlan = WLAN()

    def disconnect(self):
        pass

    def active(self, on):
        pass

    def connect(self, *a, **k):
        return (random.random() < 0.5, "", "")


class GPS:
    def read_nmea(self, max_ms=0):
        return None

    def enable(self):
        pass

    def disable(self):
        pass


class ServoScreen:
    def run_bench_sweep(self, step_x10=None, hold_ms=None, idle_cb=None):
        return random.choice([
            {"ok": True, "wind_angle_deg": 198.4, "opposite_angle_deg": 18.4, "confidence": 3.0},
            {"ok": False, "error": "no AS5600"},
            {"ok": False, "error": "sweep failed"},
        ])


class BG:
    def request_wifi_check(self, cfg, force=False):
        pass


sources = {
    "nav": lambda: Nav(), "gps": lambda: GPS(), "telemetry": lambda: Tel(),
    "cfg": lambda: config.load_config(), "wifi": lambda: Wifi(), "bg": lambda: BG(),
    "servo_screen": lambda: ServoScreen(),
    "enter_sleep": lambda: side_effects.__setitem__("sleep", side_effects["sleep"] + 1),
    "ina": lambda: types.SimpleNamespace(is_present=True),
    "ble_power_test": lambda b, r: side_effects.__setitem__("power", side_effects["power"] + 1),
}

results = []


class Svc:
    def send_result(self, v):
        results.append(bytes(v))

    def link_bonded(self):
        return True

    def mtu(self):
        return 185


def random_write(rng):
    known = list(OPCODES)
    op = rng.choice(known + [rng.randrange(256)])
    seq = rng.randrange(256)
    rule = OPCODES.get(op, (None, None))[0]
    if isinstance(rule, int):
        n = rng.choice([rule, max(0, rule - 1), rule + 1, 0, rng.randrange(40)])
    else:
        n = rng.randrange(40)
    payload = bytes(rng.randrange(256) for _ in range(n))
    shape = rng.random()
    if shape < 0.03:
        return b""
    if shape < 0.06:
        return bytes([op])
    return bytes([op, seq]) + payload


def main():
    rng = random.Random(SEED)
    random.seed(SEED)
    runner = CommandRunner(Svc(), lambda: sources)
    gpsfix.update(50.8534611, -1.4149322)
    problems = 0
    finals = 0
    import io
    import contextlib
    for i in range(ITER):
        raw = random_write(rng)
        results.clear()
        state["s"] = rng.choice(["ACQUIRE", "SAIL_NAV", "SAFE"])
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                runner.handle(raw)
                # drive any IN_PROGRESS / after work to completion
                for _ in range(400):
                    if not runner.busy():
                        break
                    clock["ms"] += 1000
                    runner.tick()
        except Exception as e:
            problems += 1
            print("RAISED on %s: %r" % (raw.hex("-"), e))
            continue
        if runner.busy():
            problems += 1
            print("STUCK BUSY after %s" % raw.hex("-"))
            continue
        final = [r for r in results if len(r) >= 3 and r[2] != IN_PROGRESS]
        if len(final) != 1:
            problems += 1
            print("%d final results for %s: %s" % (len(final), raw.hex("-"),
                                                    [r.hex("-") for r in results]))
            continue
        r = final[0]
        if len(raw) >= 2 and (r[0] != raw[0] or r[1] != raw[1]):
            problems += 1
            print("opcode/seq not echoed for %s: %s" % (raw.hex("-"), r.hex("-")))
            continue
        finals += 1

    # ERR_BUSY: a second write while one is pending
    results.clear()
    with contextlib.redirect_stdout(io.StringIO()):
        runner.handle(bytes([0x24, 1]))      # API_HANDSHAKE -> IN_PROGRESS
        busy_before = runner.busy()
        runner.handle(bytes([0x06, 2]))
    busy_ok = (not busy_before) or any(len(x) >= 3 and x[1] == 2 and x[2] == ERR_BUSY for x in results)
    if not busy_ok:
        problems += 1
        print("second write during IN_PROGRESS did not get ERR_BUSY")

    print("fuzzed %d writes (seed %d): %d clean final results, %d problems; "
          "side effects: %d reboots, %d sleeps, %d power tests requested"
          % (ITER, SEED, finals, problems, side_effects["reset"], side_effects["sleep"],
             side_effects["power"]))
    shutil.rmtree(work, ignore_errors=True)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
