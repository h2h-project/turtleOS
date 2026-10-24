# tests/journey_host.py — HOST-side (CPython) checks for v2 plan Phase B:
# journey history + events (src/app/journey.py), the scheduler's journey
# event readings and journey_log_every_s, the shared GPS fix, and the one
# battery-percent estimator. Does NOT run on the device:
#   cd device && python3 ../tests/journey_host.py
import sys, types, json, os, tempfile, time
sys.path.insert(0, os.getcwd())
# MicroPython shims
clock = [100_000]
time.ticks_ms = lambda: clock[0]
time.ticks_diff = lambda a, b: a - b
time.ticks_add = lambda a, b: a + b
time.sleep_ms = lambda ms: None
now = [1790930000]
class RTC:
    def datetime(self):
        t = time.gmtime(now[0]); return (t.tm_year, t.tm_mon, t.tm_mday, 0, t.tm_hour, t.tm_min, t.tm_sec, 0)
sys.modules["machine"] = types.SimpleNamespace(RTC=RTC)
cfg = {"set_destination": [50.1, -1.2], "telemetry_mode": "manual", "telemetry_post_every_s": 120}
sys.modules["config"] = types.SimpleNamespace(load_config=lambda: cfg)

from src.app import journey
d = tempfile.mkdtemp(); journey._STATE_PATH = os.path.join(d, "journey_state.json")
journey._read()

r = journey.start(50.0, -1.0)
assert r["id"] == 1790930000 and r["destination"] == [50.1, -1.2] and r["destination_source"] == 2, r
assert journey.active() and journey.pending_event()["event"] == "start"
now[0] += 3600
e = journey.end([50.09, -1.19])
assert e["ended_at"] == 1790933600 and e["arrival"] == [50.09, -1.19]
assert not journey.active() and journey.last()["id"] == 1790930000
on_disk = json.load(open(journey._STATE_PATH))
assert "id" not in on_disk and len(on_disk["history"]) == 1 and len(on_disk["events"]) == 2, on_disk
# Reload from disk, as after a reboot.
journey._loaded = False; journey._cache = {}; journey._history = []; journey._events = []
assert journey.history()[0]["arrival"] == [50.09, -1.19]

# Scheduler: a pending event makes tick() build a reading even in manual mode.
from src.app.telemetry_scheduler import TelemetryScheduler
class Air:
    def read_quick(self, source=None): return None
built = []
s = TelemetryScheduler(Air())
s._build_payload_parts = lambda r, rtc_temp_c=None: ({"ina_bus_v": 3.9}, None)
s._now_unix_seconds = lambda: now[0]
class BP:
    def put_payload(self, p, c): built.append(p); return True
    def result(self): return {}
s.set_background_process(BP())
cfg["turtle_mode"] = True
s.tick(cfg)
assert len(built) == 1, built
p = built[0]
assert p["schema"] == 2 and p["flags"]["journey_event"] == "start" and p["flags"]["journey"]["id"] == 1790930000, p
assert p["machine_state"] == "BOOT", p             # turtle mode: nav state rides along
assert "journey_id" not in p["flags"], p          # nothing open now
s.tick(cfg)                                        # within 10 s: no retry yet
assert len(built) == 1
s._next_event_try_ms = 0
s.tick(cfg)
assert built[1]["flags"]["journey_event"] == "end" and built[1]["flags"]["journey"]["ended_at"] == 1790933600
assert journey.pending_event() is None
s._next_event_try_ms = 0
s.tick(cfg)
assert len(built) == 2, "manual mode, no event: no reading"

# History cap
for i in range(25):
    now[0] += 100; journey.start(1.0, 1.0); now[0] += 100; journey.end(None)
assert len(journey.history()) == journey.HISTORY_MAX
assert len(json.load(open(journey._STATE_PATH))["events"]) == journey.EVENTS_MAX

# journey_log_every_s shortens the interval while a journey is open.
cfg["telemetry_mode"] = "auto"; cfg["journey_log_every_s"] = 15
journey._events.clear(); journey.start(1.0, 1.0); journey._events.clear()
s._next_send_ms = 0; t0 = time.ticks_ms(); s.tick(cfg)
assert 14_000 <= s._next_send_ms - t0 <= 16_000, s._next_send_ms - t0

# Battery percent: one estimator.
from src.drivers.ina219 import pct_from_volts
assert (pct_from_volts(3.30), pct_from_volts(4.20), pct_from_volts(3.75), pct_from_volts(None)) == (0, 100, 50, None)
print("journey + scheduler: OK")

# ---- shared GPS fix ---------------------------------------------------
from src.nav import gpsfix
from src.app.telemetry_scheduler import TelemetryScheduler
class GPS:
    def __init__(self, lines): self.lines = list(lines); self.reads = 0
    def read_nmea(self, max_ms=None):
        self.reads += 1
        return self.lines.pop(0) if self.lines else None
RMC = "$GNRMC,120000.00,A,5051.0000,N,00124.6000,W,0.1,45.0,141026,,,A*00"
GGA = "$GNGGA,120000.00,5051.0000,N,00124.6000,W,1,09,0.9,10.0,M,47.0,M,,*00"
s = TelemetryScheduler(types.SimpleNamespace(), gps=GPS([RMC, GGA]))
clock[0] = 1_000_000
# Fresh cache (nav is draining): use it, don't touch the UART.
gpsfix.update(50.5, -1.5, None)
assert s._read_gps_fix() == (50.5, -1.5) and s.gps.reads == 0
# Stale cache (airOS): drain, publish RMC + GGA, return the new fix.
clock[0] += 60_000
assert s._read_gps_fix() == (50.85, -1.41)
assert gpsfix.get()[:2] == (50.85, -1.41) and gpsfix.gga()[:2] == (1, 9)
# Nothing new and cache older than 5 min: no position.
clock[0] += 400_000
assert s._read_gps_fix() == (None, None)
print("shared GPS fix: OK")

# ---- Phase C: journeys over Bluetooth ------------------------------------
import struct
from src.net import ble_commands as C
from src.net.ble_telemetry import TurtleData
journey._TRACK_DIR = d + os.sep
jid = journey.active_id()                      # opened in the interval test above
for i in range(30):
    journey.record_point(jid, jid + i * 15, 50.8 + i * 1e-4, -1.4, "SAIL_NAV")
assert journey.track_count(jid) == 30 and journey.track_count(1) == 0
assert journey.track_page(jid, 28, 5) == journey.track_page(jid, 0, 30)[28 * 13:]
# The scheduler records a point for each reading with a fix while a journey is open.
s2 = TelemetryScheduler(Air())
s2._build_payload_parts = lambda r, rtc_temp_c=None: ({"ina_bus_v": 3.9}, None)
s2._now_unix_seconds = lambda: jid + 1000
s2._read_gps_fix = lambda *a, **k: (50.9, -1.3)
_p = s2._build_full_payload(cfg, None)
assert _p and _p["flags"]["journey_id"] == jid and _p["lat"] == 50.9, _p
assert journey.track_count(jid) == 31
assert struct.unpack("<IiiB", journey.track_page(jid, 30, 1))[:3] == (jid + 1000, 509000000, -13000000)
journey._drop_track(jid)
for i in range(30):
    journey.record_point(jid, jid + i * 15, 50.8 + i * 1e-4, -1.4, "SAIL_NAV")
# Full: thinned to every second point, keeping the newest.
tj = 42
for i in range(journey.TRACK_MAX):
    journey.record_point(tj, i, 1.0, 1.0)
assert journey.track_count(tj) == journey.TRACK_MAX // 2
assert struct.unpack("<IiiB", journey.track_page(tj, journey.TRACK_MAX // 2 - 1, 1))[0] == journey.TRACK_MAX - 1
journey._drop_track(tj)

class FakeSvc:
    def __init__(self, mtu): self._m = mtu
    def mtu(self): return self._m
ctx = {"svc": FakeSvc(185), "src": {}}
code, out, _ = C._op_journey_track(ctx, struct.pack("<IH", 0, 0))      # 0 = the open journey
total, start, n = struct.unpack("<HHB", out[:5])
assert code == C.OK and (total, start, n) == (30, 0, 13) and len(out) + 3 <= 185 - 3, (code, total, start, n)
assert struct.unpack("<IiiB", out[5:18]) == (jid, 508000000, -14000000, 2)
got = n
while got < total:
    code, out, _ = C._op_journey_track(ctx, struct.pack("<IH", jid, got))
    got += struct.unpack("<HHB", out[:5])[2]
assert got == 30
assert C._op_journey_track(ctx, struct.pack("<IH", jid, 31))[0] == C.ERR_BAD_VALUE
assert C._op_journey_track({"svc": FakeSvc(23), "src": {}}, struct.pack("<IH", jid, 0))[:2] == (C.ERR_WRONG_STATE, b"\x01")

code, out, _ = C._op_journey_list(ctx, b"\x00")
cnt, idx = struct.unpack("<BB", out[:2])
rec = struct.unpack("<IIiiiiiiB", out[2:])
newest = journey.history()[0]
assert code == C.OK and cnt == journey.HISTORY_MAX and idx == 0 and rec[0] == newest["id"] and rec[1] == newest["ended_at"]
assert rec[6] == C._NA_I32                     # those test journeys ended without a fix
assert C._op_journey_list(ctx, bytes([journey.HISTORY_MAX]))[0] == C.ERR_BAD_VALUE

v = TurtleData({}).journey()
assert struct.unpack("<IiiI", v) == (jid, 10000000, 10000000, newest["id"]) and len(v) <= 20
# A journey that falls off the history takes its track file with it.
oldest = journey.history()[-1]["id"]
journey.record_point(oldest, oldest, 1.0, 1.0)
assert journey.track_count(oldest) == 1
journey._events.clear(); journey.end(None)          # closes jid: oldest falls off
assert journey.track_count(oldest) == 0 and journey.track_count(jid) == 30
print("journeys over Bluetooth: OK")

# airOS: same payload shape, but no machine_state (the nav state machine
# never runs there, so it would always say BOOT).
journey._events.clear(); journey.end(None)
class AirR:
    def read_quick(self, source=None): return object()
s3 = TelemetryScheduler(AirR())
s3._build_payload_parts = lambda r, rtc_temp_c=None: ({"ens_eco2": 612, "aht_temp": 21.5}, {"sensor_confidence": 80})
s3._now_unix_seconds = lambda: now[0]
s3._read_gps_fix = lambda: (None, None)
p = s3._build_full_payload({"turtle_mode": False}, None)
assert p["schema"] == 2 and p["values"]["ens_eco2"] == 612 and "machine_state" not in p, p
assert "journey_id" not in p["flags"] and p["confidence"] == {"sensor_confidence": 80}, p
print("airOS payload: OK")
