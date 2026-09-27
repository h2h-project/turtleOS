# tests/ble_coex.py
# Run with: mpremote connect auto run tests/ble_coex.py
#
# Turtle Wrangler Phase 0 spike (docs/wrangler/01_firmware_ble.md), test C:
# does WiFi still behave with BLE active on the XIAO's shared 2.4 GHz radio?
#
# Connects WiFi through the real WiFiManager (so PM_PERFORMANCE is applied the
# way production does it), then runs the same batch of authenticated
# GET /api/v1/device?compact=1 calls — harmless, no telemetry written — under
# three conditions:
#     1. BLE off
#     2. BLE advertising (no phone connected)
#     3. phone connected, receiving a 5 Hz notify stream (worse than the
#        contract's real rates, deliberately)
#     4. BLE switched off again with WiFi still up (sleep / BLE_SET_ENABLED 0)
# For each it reports request latency (min/median/max), failures, and the
# INA219 current if one is on I2C_SYS. Watch for a crash/reset at any point —
# that is itself the answer.
#
# Before phase 3 the script waits (up to 3 min) for you to connect with
# nRF Connect and subscribe to the counter characteristic (…01f0).

import sys
for _p in ("/src", "/src/lib"):
    if _p not in sys.path:
        sys.path.append(_p)

import gc
import time
import json
import struct
import bluetooth
from micropython import const

N_REQUESTS = 20
GAP_MS = 1000
CURRENT_SAMPLE_S = 5
CONNECT_WAIT_S = 180
ADV_INTERVAL_US = 200_000

_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_F_READ = const(0x0002)
_F_NOTIFY = const(0x0010)


def _uuid(short):
    return bluetooth.UUID("26d0{:04x}-5890-45f2-b0be-090d35436a95".format(short))


SVC_TURTLE = _uuid(0x0001)
CH_COUNT = (_uuid(0x01F0), _F_READ | _F_NOTIFY)

_state = {"conn": None, "drops": 0}


def _irq(event, data):
    if event == _IRQ_CENTRAL_CONNECT:
        _state["conn"] = data[0]
    elif event == _IRQ_CENTRAL_DISCONNECT:
        _state["conn"] = None
        _state["drops"] += 1


def _adv_field(t, v):
    return struct.pack("BB", len(v) + 1, t) + v


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) // 2


# ---- config ----
# Prefer a test config copied to the board for this run (e.g. the England WiFi
# credentials), so the turtle's own /config.json is never modified:
#   mpremote cp scripts/xiao_config_england.json :xiao_config_england.json
CFG_CANDIDATES = ("/xiao_config_england.json", "/config.json")
cfg, cfg_path = None, None
for _p in CFG_CANDIDATES:
    try:
        with open(_p) as f:
            cfg, cfg_path = json.load(f), _p
        break
    except OSError:
        pass
if cfg is None:
    raise SystemExit("no config found (tried %s)" % (CFG_CANDIDATES,))
api_base = str(cfg.get("api_base") or "").rstrip("/").replace("https://", "http://")
headers = {"X-Device-Id": str(cfg.get("device_id") or ""),
           "X-Device-Key": str(cfg.get("device_key") or "")}
name = str(cfg.get("device_name") or "") or ("Turtle-" + (headers["X-Device-Id"][-4:] or "0000"))
ssid = str(cfg.get("wifi_ssid") or "")

print("=" * 52)
print("  Turtle Wrangler WiFi + BLE coexistence (Phase 0, test C)")
print("=" * 52)
print("[CFG] from %s  api_base: %s  ssid: %r" % (cfg_path, api_base, ssid))

# ---- start from a clean radio: BLE must be off for the baseline ----
_b = bluetooth.BLE()
if _b.active():
    print("[BLE] WARNING: BLE was still active from a previous run - turning it off")
    _b.active(False)
    time.sleep_ms(300)
else:
    print("[BLE] off at start (good)")

# ---- power-management constants ----
import network
for c in ("PM_NONE", "PM_PERFORMANCE", "PM_POWERSAVE"):
    print("[PM] network.WLAN.%s = %r" % (c, getattr(network.WLAN, c, "MISSING")))

# ---- WiFi via the production manager ----
from src.net.wifi_manager import WiFiManager
wm = WiFiManager()
# Radio off first so connect() takes the hard-reset path, clearing any
# association a previous run (or a soft reset) left half-finished.
wm.active(False)
time.sleep_ms(200)
wm.wlan.active(True)
try:
    seen = [n for n in wm.wlan.scan() if n[0].decode("utf-8", "ignore") == ssid]
    if seen:
        n = seen[0]
        print("[SCAN] %r visible: ch %d rssi %d dBm authmode %d" % (ssid, n[2], n[3], n[4]))
    else:
        print("[SCAN] %r NOT visible - check the SSID / that it's a 2.4 GHz network" % ssid)
except Exception as e:
    print("[SCAN] failed:", repr(e))
wm.active(False)
time.sleep_ms(200)
ok, ip, st = wm.connect(ssid, cfg.get("wifi_password"), timeout_s=12, retry=0)
print("[WIFI] connect:", ok, ip, st, " last_error=%r" % wm.last_error())
if not ok:
    print("[WIFI] wlan.status() =", wm.status_code())
    raise SystemExit("WiFi did not connect - see [SCAN]/[WIFI] lines above")
try:
    print("[PM] wlan pm now =", wm.wlan.config("pm"))
except Exception as e:
    print("[PM] pm readback failed:", repr(e))

# ---- INA219 (optional) ----
ina = None
try:
    from src.hal.board import init_i2c
    from src.drivers.ina219 import INA219
    _i2c = init_i2c()
    if 0x40 in _i2c.scan():
        ina = INA219(_i2c)
        print("[INA] INA219 found at 0x40")
    else:
        print("[INA] no INA219 at 0x40 - current readings skipped")
except Exception as e:
    print("[INA] init failed:", repr(e))

import urequests as requests

ble = None
h_count = None
count = 0


def _notify_tick():
    """Push one counter notification if a phone is connected."""
    global count
    if ble is None or h_count is None:
        return
    count += 1
    v = struct.pack("<I", count)
    ble.gatts_write(h_count, v)
    conn = _state["conn"]
    if conn is not None:
        try:
            ble.gatts_notify(conn, h_count, v)
        except Exception:
            pass


def _wait(ms, notify_hz):
    """Sleep ms, emitting notifications at notify_hz meanwhile (0 = none)."""
    step = (1000 // notify_hz) if notify_hz else ms
    end = time.ticks_add(time.ticks_ms(), ms)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        if notify_hz:
            _notify_tick()
        time.sleep_ms(min(step, max(1, time.ticks_diff(end, time.ticks_ms()))))


def _current(notify_hz):
    if ina is None:
        return None
    vals = []
    end = time.ticks_add(time.ticks_ms(), CURRENT_SAMPLE_S * 1000)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        try:
            r = ina.read()
            if r.get("current_ma") is not None:
                vals.append(r["current_ma"])
        except Exception:
            pass
        _wait(100, notify_hz)
    if not vals:
        return None
    return (min(vals), sum(vals) / len(vals), max(vals))


def _run_phase(label, notify_hz):
    print()
    print("---- %s ----" % label)
    cur = _current(notify_hz)
    if cur:
        print("[INA] current mA  min %.1f  avg %.1f  max %.1f" % cur)
    lat, fails = [], []
    no_phone = 0
    drops0 = _state["drops"]
    url = api_base + "/api/v1/device?compact=1"
    for i in range(N_REQUESTS):
        if notify_hz and _state["conn"] is None:
            no_phone += 1
        gc.collect()
        wm._apply_pm_performance(quiet=True)   # production re-applies before each request
        t0 = time.ticks_ms()
        try:
            r = requests.get(url, headers=headers, timeout=8)
            code = r.status_code
            r.close()
            dt = time.ticks_diff(time.ticks_ms(), t0)
            if 200 <= code < 300:
                lat.append(dt)
            else:
                fails.append("HTTP %d" % code)
            print("  req %2d: %4d ms  HTTP %d  conn=%s" % (i + 1, dt, code, _state["conn"]))
        except Exception as e:
            dt = time.ticks_diff(time.ticks_ms(), t0)
            fails.append(repr(e))
            print("  req %2d: %4d ms  FAIL %r" % (i + 1, dt, e))
        _wait(GAP_MS, notify_hz)
    if notify_hz and (no_phone or _state["drops"] != drops0):
        fails.append("INVALID: phone absent for %d/%d requests (%d disconnect(s))"
                     % (no_phone, N_REQUESTS, _state["drops"] - drops0))
    res = {"label": label, "n_ok": len(lat), "fails": fails, "cur": cur,
           "min": min(lat) if lat else None, "med": _median(lat), "max": max(lat) if lat else None,
           "wifi": wm.is_connected()}
    print("[RESULT] ok %d/%d  min %s  median %s  max %s ms  wifi_connected=%s"
          % (res["n_ok"], N_REQUESTS, res["min"], res["med"], res["max"], res["wifi"]))
    return res


results = []

# ---- phase 1: BLE off ----
results.append(_run_phase("1. BLE off", 0))

# ---- phase 2: BLE advertising ----
ble = bluetooth.BLE()
ble.irq(_irq)
ble.active(True)
try:
    ble.config(gap_name=name)
except Exception:
    pass
((h_count,),) = ble.gatts_register_services(((SVC_TURTLE, (CH_COUNT,)),))
adv = _adv_field(0x01, b"\x06") + _adv_field(0x07, bytes(SVC_TURTLE))
resp = _adv_field(0x09, name.encode()[:29])
ble.gap_advertise(ADV_INTERVAL_US, adv_data=adv, resp_data=resp, connectable=True)
print()
print("[BLE] active, advertising as %r - DON'T connect the phone yet" % name)
print("[WIFI] still connected after BLE activate:", wm.is_connected())
results.append(_run_phase("2. BLE advertising", 0))

# ---- phase 3: phone connected, 5 Hz notifications ----
print()
print(">>> NOW: in nRF Connect, connect to %r and subscribe (triple-arrow)" % name)
print(">>>      to the characteristic ending in ...01f0. Waiting up to %d s." % CONNECT_WAIT_S)
end = time.ticks_add(time.ticks_ms(), CONNECT_WAIT_S * 1000)
while _state["conn"] is None and time.ticks_diff(end, time.ticks_ms()) > 0:
    time.sleep_ms(200)
if _state["conn"] is None:
    print("[BLE] no phone connected - skipping phase 3")
else:
    print("[BLE] phone connected; 10 s to subscribe before measuring...")
    d0 = _state["drops"]
    _wait(10_000, 5)
    if _state["drops"] != d0 or _state["conn"] is None:
        print("[BLE] !! phone DISCONNECTED during the subscribe window.")
        print("[BLE] !! Most likely the phone is still bonded from ble_spike.py and this")
        print("[BLE] !! script has no bond keys, so encryption fails and Android drops the")
        print("[BLE] !! link. Forget 'Turtle-18' in Android Bluetooth settings, then connect.")
        end = time.ticks_add(time.ticks_ms(), CONNECT_WAIT_S * 1000)
        while _state["conn"] is None and time.ticks_diff(end, time.ticks_ms()) > 0:
            time.sleep_ms(200)
        if _state["conn"] is not None:
            print("[BLE] reconnected; 10 s to subscribe...")
            _wait(10_000, 5)
    results.append(_run_phase("3. phone connected + 5 Hz notify", 5))
    print("[BLE] phone still connected at end:", _state["conn"] is not None)

# ---- phase 4: BLE switched off with WiFi up (what sleep / BLE_SET_ENABLED 0 do) ----
ble.active(False)
ble = None
print()
print("[BLE] deactivated; WiFi still connected:", wm.is_connected())
results.append(_run_phase("4. BLE off again (after use)", 0))

# ---- summary ----
print()
print("=" * 52)
print("  SUMMARY  (paste this block back)")
print("=" * 52)
for r in results:
    cur = ("avg %.0f mA" % r["cur"][1]) if r["cur"] else "no INA"
    print("%-34s ok %2d/%d  med %4s  max %4s ms  %s  wifi=%s"
          % (r["label"], r["n_ok"], N_REQUESTS, r["med"], r["max"], cur, r["wifi"]))
    for f in r["fails"]:
        print("    fail:", f)
try:
    print("[PM] wlan pm at end =", wm.wlan.config("pm"))
except Exception:
    pass
print("[DONE] BLE off. WiFi left connected.")
