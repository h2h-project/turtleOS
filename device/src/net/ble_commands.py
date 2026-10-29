# src/net/ble_commands.py — Turtle Wrangler command channel (Phase 4)
#
# GATT contract v1 → Command protocol (docs/wrangler/README.md):
#   Command 0201 (write):  <BB> opcode, seq  + payload
#   Result  0202 (notify): <BBB> opcode, seq, code  + payload
#
# Rules implemented here:
#   - every command gets exactly ONE final result; slow ones first send
#     IN_PROGRESS with the same seq;
#   - one command at a time: anything arriving meanwhile gets ERR_BUSY;
#   - framing / length / bond checks happen before a handler runs, each with
#     its own result code, so the app always learns why;
#   - commands run from BleService.tick() on the main loop — the IRQ handler
#     only queued the raw bytes. FAST_OPCODES also run from fast_tick() on
#     every button poll, so the app's servo dial isn't held to the ~500 ms
#     _bg_tick cadence.
#
# Handlers live in OPCODES and call src.app.actions (the same code the OLED
# gestures use). A handler returns (code, payload_bytes, followup):
#   followup None                  nothing more to do
#   ("after", ms, fn)              run fn() ms after the final result went out
#                                  (reboot, radio off — the phone must get the
#                                  result before the link drops)
#   ("poll", fn, timeout_ms)       code was IN_PROGRESS; fn() returns None while
#                                  still running, else (code, payload_bytes)

import struct
import time

# ---- result codes (mirror src/app/actions.py) ----
OK = 0x00
IN_PROGRESS = 0x01
ERR_UNKNOWN_OPCODE = 0x10
ERR_BAD_LENGTH = 0x11
ERR_BAD_VALUE = 0x12
ERR_BUSY = 0x13
ERR_NOT_BONDED = 0x14
ERR_WRONG_STATE = 0x15
ERR_NO_HARDWARE = 0x22
ERR_WIFI_FAILED = 0x30
ERR_INTERNAL = 0x7F

AFTER_RESULT_MS = 300     # give the Result notification time to go out


# ---------------------------------------------------------------- handlers
# Signature: fn(ctx, payload) -> (code, payload_bytes, followup)
# ctx: {"svc": BleService, "src": sources dict of getters}

def _op_ble_set_enabled(ctx, payload):
    """0x33 BLE_SET_ENABLED <B> — only 0 is accepted: once Bluetooth is off,
    only the turtle's Bluetooth screen can turn it back on."""
    if payload[0] != 0:
        return ERR_BAD_VALUE, b"", None

    def _off():
        from src.app import actions
        actions.ble_set_enabled(False)

    return OK, b"", ("after", AFTER_RESULT_MS, _off)


def _op_reboot(ctx, payload):
    """0x42 REBOOT — OK, then machine.reset()."""
    def _reset():
        print("[BLE] reboot requested over Bluetooth")
        import machine
        machine.reset()

    return OK, b"", ("after", AFTER_RESULT_MS, _reset)


# ---- Phase 5: journey, destination, GPS & logging ----------------------
# Each calls the same src.app.actions function as its OLED gesture; the
# action's result code is already the contract's, so only payloads differ.

def _get(ctx, key):
    try:
        g = ctx["src"].get(key)
        return g() if g is not None else None
    except Exception:
        return None


def _e7(deg):
    return int(round(float(deg) * 1e7))


def _op_journey_start(ctx, payload):
    """0x01 JOURNEY_START — uses the turtle's own fix. OK <I> journey_id."""
    from src.app import actions
    code, info = actions.journey_start(_get(ctx, "gps"))
    if code == actions.OK:
        return code, struct.pack("<I", int(info.get("journey_id") or 0) & 0xFFFFFFFF), None
    return code, b"", None


def _op_journey_end(ctx, payload):
    """0x02 JOURNEY_END — OK <B> arrival_stamped (0 = no fix; still ended)."""
    from src.app import actions
    code, info = actions.journey_end(_get(ctx, "gps"))
    if code == actions.OK:
        return code, struct.pack("<B", 1 if info.get("arrival_stamped") else 0), None
    return code, b"", None


_NA_I32 = 0x7FFFFFFF
_RESULT_MAX = 180     # ble_service.RESULT_BUFFER
_RESULT_HDR = 3       # opcode, seq, code


def _pt_e7(pair):
    """[lat, lon] → (lat_e7, lon_e7), sentinels when missing."""
    try:
        return _e7(pair[0]), _e7(pair[1])
    except Exception:
        return _NA_I32, _NA_I32


def _op_journey_list(ctx, payload):
    """0x07 JOURNEY_LIST <B> index (0 = newest ended journey).
    OK <BB> count, index + record <IIiiiiiiB>; BAD_VALUE past the end."""
    from src.app import journey
    (i,) = struct.unpack("<B", payload)
    hist = journey.history()
    if i >= len(hist):
        return ERR_BAD_VALUE, b"", None
    r = hist[i]
    dep = _pt_e7(r.get("departure"))
    dest = _pt_e7(r.get("destination"))
    arr = _pt_e7(r.get("arrival"))
    src = r.get("destination_source")
    src = int(src) if isinstance(src, int) and 0 <= src < 0xFF else 0xFF
    rec = struct.pack("<IIiiiiiiB", int(r.get("id") or 0) & 0xFFFFFFFF,
                      int(r.get("ended_at") or 0) & 0xFFFFFFFF,
                      dep[0], dep[1], dest[0], dest[1], arr[0], arr[1], src)
    return OK, struct.pack("<BB", min(len(hist), 255), i) + rec, None


def _op_journey_track(ctx, payload):
    """0x08 JOURNEY_TRACK <IH> journey_id (0 = the open one), start.
    OK <HHB> total, start, n + n x <IiiB> points from the journey's track
    file, as many as fit the link MTU. WRONG_STATE <B> 0 no journey open,
    1 MTU too small; BAD_VALUE start > total. One seek and one small read:
    this runs inside the button loop, so it must stay cheap."""
    from src.app import journey
    jid, start = struct.unpack("<IH", payload)
    if jid == 0:
        jid = int(journey.active_id() or 0)
        if jid == 0:
            return ERR_WRONG_STATE, b"\x00", None
    try:
        mtu = int(ctx["svc"].mtu())
    except Exception:
        mtu = 23
    room = min(mtu - 3, _RESULT_MAX) - _RESULT_HDR - 5
    fit = room // journey.TRACK_POINT_SIZE
    if fit < 1:
        return ERR_WRONG_STATE, b"\x01", None
    total = min(journey.track_count(jid), 0xFFFF)
    if start > total:
        return ERR_BAD_VALUE, b"", None
    pts = journey.track_page(jid, start, min(fit, total - start))
    n = len(pts) // journey.TRACK_POINT_SIZE
    return OK, struct.pack("<HHB", total, start, n) + pts, None


def _op_dest_set_here(ctx, payload):
    """0x03 DEST_SET_HERE — the turtle's position. OK <ii> stamped lat/lon."""
    from src.app import actions
    code, info = actions.dest_set_here(_get(ctx, "gps"))
    if code == actions.OK:
        return code, struct.pack("<ii", _e7(info["lat"]), _e7(info["lon"])), None
    return code, b"", None


def _op_dest_set_mission(ctx, payload):
    """0x04 DEST_SET_MISSION."""
    from src.app import actions
    code, _info = actions.dest_set_mission()
    return code, b"", None


def _op_dest_set_coords(ctx, payload):
    """0x05 DEST_SET_COORDS <ii> lat_e7, lon_e7 (app map picker)."""
    from src.app import actions
    lat_e7, lon_e7 = struct.unpack("<ii", payload)
    code, _info = actions.dest_set_coords(lat_e7 / 1e7, lon_e7 / 1e7)
    return code, b"", None


def _op_dest_clear(ctx, payload):
    """0x06 DEST_CLEAR."""
    from src.app import actions
    code, _info = actions.dest_clear()
    return code, b"", None


def _op_gps_stamp(ctx, payload):
    """0x20 GPS_STAMP — manual mode only.
    OK <BHB> committed_to, stamps_session, has_position
    ERR_NOT_STAMPED <B> reason"""
    from src.app import actions
    code, info = actions.gps_stamp(_get(ctx, "telemetry"), _get(ctx, "cfg"))
    if code == actions.OK:
        return code, struct.pack("<BHB", int(info.get("committed_to", 1)),
                                 min(int(info.get("stamps_session", 0)), 0xFFFE),
                                 1 if info.get("has_position") else 0), None
    if code == actions.ERR_NOT_STAMPED:
        return code, struct.pack("<B", int(info.get("reason", 0xFF)) & 0xFF), None
    return code, b"", None


def _op_gps_set_enabled(ctx, payload):
    """0x21 GPS_SET_ENABLED <B> 0/1."""
    if payload[0] > 1:
        return ERR_BAD_VALUE, b"", None
    from src.app import actions
    code, _info = actions.gps_set_enabled(_get(ctx, "gps"), payload[0] == 1)
    return code, b"", None


_MODES = ("off", "auto", "manual")


def _op_telemetry_set_mode(ctx, payload):
    """0x22 TELEMETRY_SET_MODE <B> 0 off · 1 auto · 2 manual."""
    if payload[0] >= len(_MODES):
        return ERR_BAD_VALUE, b"", None
    from src.app import actions
    code, _info = actions.telemetry_set_mode(_MODES[payload[0]])
    return code, b"", None


def _op_telemetry_set_interval(ctx, payload):
    """0x23 TELEMETRY_SET_INTERVAL <H> seconds (>= 10)."""
    from src.app import actions
    (secs,) = struct.unpack("<H", payload)
    code, _info = actions.telemetry_set_interval(secs)
    return code, b"", None


API_HANDSHAKE_TIMEOUT_MS = 30_000


def _op_api_handshake(ctx, payload):
    """0x24 API_HANDSHAKE — IN_PROGRESS, then OK / ERR_API_FAILED once the
    background sender records a new attempt (ERR_API_FAILED after 30 s)."""
    from src.app import actions
    tel = _get(ctx, "telemetry")
    code, info = actions.api_handshake(tel, _get(ctx, "cfg"))
    if code != actions.IN_PROGRESS:
        return code, b"", None
    before = info.get("before_ms")
    deadline = time.ticks_add(time.ticks_ms(), API_HANDSHAKE_TIMEOUT_MS)

    def _poll():
        r = actions.api_outcome(tel, before)
        if r is not None:
            return r, b""
        if time.ticks_diff(time.ticks_ms(), deadline) >= 0:
            return actions.ERR_API_FAILED, b""
        return None

    return IN_PROGRESS, b"", ("poll", _poll, API_HANDSHAKE_TIMEOUT_MS + 5_000)


# ---- Phase 6: sail, connectivity, system ----------------------------------

NA_U16 = 0xFFFF
NAV_SWEEP_TIMEOUT_MS = 180_000
BENCH_SWEEP_TIMEOUT_MS = 300_000
WIFI_CONNECT_TIMEOUT_MS = 25_000


def _x10(deg):
    if deg is None:
        return NA_U16
    try:
        n = int(round((float(deg) % 360.0) * 10))
        return n if 0 <= n < NA_U16 else NA_U16
    except Exception:
        return NA_U16


def _op_nav_luff_sweep(ctx, payload):
    """0x10 NAV_LUFF_SWEEP — the autonomous sweep. IN_PROGRESS, then
    OK <H> wind_x10 when it finishes (0xFFFF: ended without a wind angle).
    ERR_WRONG_STATE <B> nav state outside ACQUIRE / SAIL_NAV."""
    from src.app import actions
    nav = _get(ctx, "nav")
    code, info = actions.nav_luff_sweep(nav)
    if code == actions.ERR_WRONG_STATE:
        states = {"BOOT": 0, "ACQUIRE": 1, "SAIL_NAV": 2, "ARRIVAL": 3, "SAFE": 4}
        return code, struct.pack("<B", states.get(info.get("state"), 0xFF)), None
    if code != actions.IN_PROGRESS:
        return code, b"", None

    def _poll():
        try:
            if nav.sweeping():
                return None
            return OK, struct.pack("<H", _x10(nav.wind_angle()))
        except Exception:
            return ERR_INTERNAL, b""

    return IN_PROGRESS, b"", ("poll", _poll, NAV_SWEEP_TIMEOUT_MS)


def _op_servo_bench_sweep(ctx, payload):
    """0x11 SERVO_BENCH_SWEEP — WindFinder on the real servo. Blocks the main
    loop for the whole sweep (it owns the servo; nav is not ticked), drawing
    its progress on the OLED. IN_PROGRESS first, then
    OK <HHB> wind_x10, alt_wind_x10, confidence_pct."""
    cfg = _get(ctx, "cfg") or {}
    if not cfg.get("servo_present", False):
        return ERR_NO_HARDWARE, b"", None
    scr = _get(ctx, "servo_screen")
    if scr is None or not hasattr(scr, "run_bench_sweep"):
        return ERR_NO_HARDWARE, b"", None
    # Start one tick later so the IN_PROGRESS notification is on its way
    # before the main loop disappears into the sweep.
    start_at = time.ticks_add(time.ticks_ms(), 300)

    def _poll():
        if time.ticks_diff(time.ticks_ms(), start_at) < 0:
            return None
        from src.app import actions
        r = scr.run_bench_sweep()
        if not r.get("ok"):
            print("[BLE] bench sweep:", r.get("error"))
            if r.get("error") in ("no servo", "no AS5600"):
                return ERR_NO_HARDWARE, b""
            return ERR_INTERNAL, b""
        conf = actions.bench_confidence_pct(r.get("confidence"))
        return OK, struct.pack("<HHB", _x10(r.get("wind_angle_deg")),
                               _x10(r.get("opposite_angle_deg")),
                               0xFF if conf is None else conf)

    return IN_PROGRESS, b"", ("poll", _poll, BENCH_SWEEP_TIMEOUT_MS)


def _wifi_real(wifi):
    return wifi is not None and getattr(wifi, "wlan", None) is not None


def _wifi_ip(wifi):
    """IP via ifconfig() — reads lwIP, safe while WPA2 auth holds the WiFi
    driver mutex (isconnected() is not)."""
    try:
        ip = wifi.wlan.ifconfig()[0]
        return ip if ip and ip != "0.0.0.0" else None
    except Exception:
        return None


def _wifi_attempt(ctx):
    """Start an association attempt with the saved credentials and return a
    poll fn -> None while trying, else (OK | ERR_WIFI_FAILED, b"")."""
    from config import load_config
    cfg = load_config() or {}
    wifi = _get(ctx, "wifi")
    bg = _get(ctx, "bg")
    deadline = time.ticks_add(time.ticks_ms(), WIFI_CONNECT_TIMEOUT_MS)

    if _wifi_real(wifi) and bg is not None:
        # The background thread owns WiFi: ask it (forced = deliberate user
        # action, bypasses backoff and mission_connection_mode) and watch the
        # IP — never touch the radio from the main thread.
        bg.request_wifi_check(cfg, force=True)

        def _poll_bg():
            if _wifi_ip(wifi):
                return OK, b""
            if time.ticks_diff(time.ticks_ms(), deadline) >= 0:
                return ERR_WIFI_FAILED, b""
            return None
        return _poll_bg

    # WiFi was off at boot (NullWiFiManager) or no background thread:
    # connect inline like the WiFi screen does (12 s, under the RTC WDT).
    def _poll_inline():
        try:
            if _wifi_real(wifi):
                m = wifi
            else:
                from src.net.wifi_manager import WiFiManager
                m = WiFiManager()
            ok, _ip, _st = m.connect(cfg.get("wifi_ssid"), cfg.get("wifi_password"),
                                     timeout_s=12, retry=0)
            return (OK if ok else ERR_WIFI_FAILED), b""
        except Exception as e:
            print("[BLE] wifi connect err:", repr(e))
            return ERR_WIFI_FAILED, b""
    return _poll_inline


def _op_wifi_set_enabled(ctx, payload):
    """0x30 WIFI_SET_ENABLED <B> — on: IN_PROGRESS -> OK / ERR_WIFI_FAILED;
    off: OK (radio dropped)."""
    from src.app import actions
    if payload[0] > 1:
        return ERR_BAD_VALUE, b"", None
    wifi = _get(ctx, "wifi")
    if payload[0] == 0:
        code, _info = actions.wifi_set_enabled(False, wifi if _wifi_real(wifi) else None)
        return code, b"", None
    code, _info = actions.wifi_set_enabled(True)
    if code != actions.OK:
        return code, b"", None
    return IN_PROGRESS, b"", ("poll", _wifi_attempt(ctx), WIFI_CONNECT_TIMEOUT_MS + 5_000)


def _op_wifi_set_credentials(ctx, payload):
    """0x31 WIFI_SET_CREDENTIALS <B> ssid_len, ssid, password (rest).
    Saves first; then, if WiFi is enabled, drops the current network and
    reconnects: IN_PROGRESS -> OK / ERR_WIFI_FAILED (still saved)."""
    from src.app import actions
    n = payload[0]
    if len(payload) < 1 + n or n == 0:
        return ERR_BAD_LENGTH, b"", None
    try:
        ssid = bytes(payload[1:1 + n]).decode()
        password = bytes(payload[1 + n:]).decode()
    except Exception:
        return ERR_BAD_VALUE, b"", None
    code, _info = actions.wifi_set_credentials(ssid, password)
    if code != actions.OK:
        return code, b"", None
    cfg = _get(ctx, "cfg") or {}
    if not cfg.get("wifi_enabled", False):
        return OK, b"", None
    wifi = _get(ctx, "wifi")
    if _wifi_real(wifi):
        try:
            wifi.disconnect()   # a reconnect only happens when not connected
        except Exception:
            pass
    return IN_PROGRESS, b"", ("poll", _wifi_attempt(ctx), WIFI_CONNECT_TIMEOUT_MS + 5_000)


def _op_wifi_get_ssid(ctx, payload):
    """0x34 WIFI_GET_SSID — OK + the saved SSID (UTF-8, empty = none). Never
    the password: it is write-only over Bluetooth."""
    cfg = _get(ctx, "cfg") or {}
    try:
        ssid = str(cfg.get("wifi_ssid") or "").strip().encode()[:32]
    except Exception:
        ssid = b""
    return OK, ssid, None


_CONN_MODES = ("wifi_auto", "wifi_manual")


def _op_connection_mode_set(ctx, payload):
    """0x32 CONNECTION_MODE_SET <B> 0 wifi_auto · 1 wifi_manual (2 lora reserved)."""
    if payload[0] >= len(_CONN_MODES):
        return ERR_BAD_VALUE, b"", None
    from src.app import actions
    code, _info = actions.connection_mode_set(_CONN_MODES[payload[0]])
    return code, b"", None


def _op_sleep(ctx, payload):
    """0x40 SLEEP — OK, then the same Good night -> sleep -> wake flow as the
    Sleep screen (Bluetooth shuts down while asleep)."""
    enter = (ctx["src"] or {}).get("enter_sleep")
    if enter is None:
        return ERR_NO_HARDWARE, b"", None
    return OK, b"", ("after", AFTER_RESULT_MS, enter)


def _op_set_turtle_mode(ctx, payload):
    """0x41 SET_TURTLE_MODE <B> — 0 = switch to airOS and reboot (no BLE
    there); 1 = already turtleOS, nothing to do."""
    if payload[0] > 1:
        return ERR_BAD_VALUE, b"", None
    if payload[0] == 1:
        return OK, b"", None
    from src.app import actions
    code, _info = actions.set_turtle_mode(False)
    if code != actions.OK:
        return code, b"", None

    def _reset():
        print("[BLE] switching to airOS - rebooting")
        import machine
        machine.reset()

    return OK, b"", ("after", AFTER_RESULT_MS, _reset)


def _op_servo_set_angle(ctx, payload):
    """0x12 SERVO_SET_ANGLE <B> 0..180 of the servo's full travel. OK <H>
    the pulse written (us). Pauses autopilot servo writes for 60 s. The
    app's dial sends a stream of these while dragging, one at a time."""
    from src.app import actions
    code, info = actions.servo_set_angle(_get(ctx, "nav"), payload[0])
    if code == OK:
        return code, struct.pack("<H", info["us"]), None
    return code, b"", None


def _op_compass_set_offset(ctx, payload):
    """0x43 COMPASS_SET_OFFSET <h> degrees, -180..180; applied live."""
    from src.app import actions
    (deg,) = struct.unpack("<h", payload)
    heading = None
    nav = _get(ctx, "nav")
    if nav is not None:
        try:
            heading = nav.heading_source()
        except Exception:
            heading = None
    code, _info = actions.compass_set_offset(deg, heading)
    return code, b"", None


def _op_time_set(ctx, payload):
    """0x44 TIME_SET <I> unix seconds UTC (the phone's clock). OK <B>
    rtc_chip_written: 1 = the DS3231 was set too, 0 = system clock only."""
    from src.app import actions
    (secs,) = struct.unpack("<I", payload)
    code, info = actions.time_set(secs, _get(ctx, "i2c"), _get(ctx, "rtc"))
    if code == OK:
        return code, struct.pack("<B", 1 if info.get("rtc_chip") else 0), None
    return code, b"", None


def _op_timezone_set(ctx, payload):
    """0x46 TIMEZONE_SET <h> minutes from UTC, -720..840."""
    from src.app import actions
    (m,) = struct.unpack("<h", payload)
    code, _info = actions.timezone_set(m)
    return code, b"", None


def _op_secure_mode_set(ctx, payload):
    """0x45 SECURE_MODE_SET <B> 0/1. WRONG_STATE <B> 1 = turning on refused
    (RTC battery fault)."""
    from src.app import actions
    code, info = actions.secure_mode_set(payload[0])
    if code == actions.ERR_WRONG_STATE:
        return code, struct.pack("<B", info.get("reason", 0xFF)), None
    return code, b"", None


def _op_ble_power_test(ctx, payload):
    """0x47 BLE_POWER_TEST <BB> block_s 20..255, rounds 1..10. OK <H> the
    seconds it will take, then src/net/ble_power_test.run() drops the link
    and owns the main loop until it's done. WRONG_STATE <B> 0 = a journey
    is open (the autopilot would stop steering for the whole test)."""
    block_s, rounds = payload[0], payload[1]
    if block_s < 20 or not 1 <= rounds <= 10:
        return ERR_BAD_VALUE, b"", None
    ina = _get(ctx, "ina")
    start = (ctx["src"] or {}).get("ble_power_test")
    if ina is None or not getattr(ina, "is_present", False) or start is None:
        return ERR_NO_HARDWARE, b"", None
    try:
        from src.app import journey
        if journey.active_id():
            return ERR_WRONG_STATE, b"\x00", None
    except Exception:
        pass
    from src.net import ble_power_test
    secs = min(ble_power_test.duration_s(block_s, rounds), 0xFFFF)
    return OK, struct.pack("<H", secs), ("after", AFTER_RESULT_MS, lambda: start(block_s, rounds))


def _op_ble_power_result(ctx, payload):
    """0x48 BLE_POWER_RESULT — OK + the last test's results. WRONG_STATE
    <B> 0 = no test has run, 1 = the link's MTU can't carry them."""
    from src.net import ble_power_test
    res = ble_power_test.load()
    if not res:
        return ERR_WRONG_STATE, b"\x00", None
    out = ble_power_test.result_payload(res)
    try:
        mtu = int(ctx["svc"].mtu())
    except Exception:
        mtu = 23
    if _RESULT_HDR + len(out) > min(mtu - 3, _RESULT_MAX):
        return ERR_WRONG_STATE, b"\x01", None
    return OK, out, None


# opcode -> (payload length, handler). Length: int = exact, ("min", n) = at
# least n, None = anything.
OPCODES = {
    0x01: (0, _op_journey_start),
    0x02: (0, _op_journey_end),
    0x03: (0, _op_dest_set_here),
    0x04: (0, _op_dest_set_mission),
    0x05: (8, _op_dest_set_coords),
    0x06: (0, _op_dest_clear),
    0x07: (1, _op_journey_list),
    0x08: (6, _op_journey_track),
    0x20: (0, _op_gps_stamp),
    0x21: (1, _op_gps_set_enabled),
    0x22: (1, _op_telemetry_set_mode),
    0x23: (2, _op_telemetry_set_interval),
    0x24: (0, _op_api_handshake),
    0x10: (0, _op_nav_luff_sweep),
    0x11: (0, _op_servo_bench_sweep),
    0x12: (1, _op_servo_set_angle),
    0x30: (1, _op_wifi_set_enabled),
    0x31: (("min", 2), _op_wifi_set_credentials),
    0x32: (1, _op_connection_mode_set),
    0x33: (1, _op_ble_set_enabled),
    0x34: (0, _op_wifi_get_ssid),
    0x40: (0, _op_sleep),
    0x41: (1, _op_set_turtle_mode),
    0x42: (0, _op_reboot),
    0x43: (2, _op_compass_set_offset),
    0x44: (4, _op_time_set),
    0x45: (1, _op_secure_mode_set),
    0x46: (2, _op_timezone_set),
    0x47: (2, _op_ble_power_test),
    0x48: (0, _op_ble_power_result),
}


# Opcodes quick enough to run from a button poll (BleService.fast_tick):
# no I/O beyond a PWM write, never IN_PROGRESS, never blocking.
FAST_OPCODES = (0x12,)   # SERVO_SET_ANGLE


def _length_ok(rule, n):
    if rule is None:
        return True
    if isinstance(rule, tuple):
        return n >= rule[1]
    return n == rule


# ---------------------------------------------------------------- runner

class CommandRunner:
    def __init__(self, svc, sources_getter):
        self._svc = svc
        self._sources = sources_getter     # () -> sources dict (may be None)
        self._pending = None               # dict while a command is running

    def busy(self):
        return self._pending is not None

    def cancel(self):
        """Link dropped: forget any in-flight command (its result can't be
        delivered; the app reads Result after reconnecting)."""
        p = self._pending
        self._pending = None
        # A queued reboot / radio-off still happens — the phone asked for it.
        if p is not None and p.get("kind") == "after":
            try:
                p["fn"]()
            except Exception as e:
                print("[BLE] after-cancel action err:", repr(e))

    def _cfg(self):
        src = self._sources() or {}
        try:
            return src.get("cfg")() or {}
        except Exception:
            return {}

    def _send(self, op, seq, code, payload=b""):
        self._svc.send_result(struct.pack("<BBB", op & 0xFF, seq & 0xFF, code) + payload)
        print("[BLE] cmd 0x%02x seq %d -> 0x%02x" % (op, seq, code))

    def handle(self, raw):
        """One raw write from the Command characteristic."""
        if len(raw) < 2:
            self._send(raw[0] if raw else 0, 0, ERR_BAD_LENGTH)
            return
        op, seq, payload = raw[0], raw[1], bytes(raw[2:])

        if self._pending is not None:
            self._send(op, seq, ERR_BUSY)
            return
        entry = OPCODES.get(op)
        if entry is None:
            self._send(op, seq, ERR_UNKNOWN_OPCODE)
            return
        rule, fn = entry
        if not _length_ok(rule, len(payload)):
            self._send(op, seq, ERR_BAD_LENGTH)
            return
        if self._cfg().get("ble_require_bond", True) and not self._svc.link_bonded():
            self._send(op, seq, ERR_NOT_BONDED)
            return

        try:
            code, out, follow = fn({"svc": self._svc, "src": self._sources() or {}}, payload)
        except Exception as e:
            print("[BLE] cmd 0x%02x handler err: %r" % (op, e))
            self._send(op, seq, ERR_INTERNAL)
            return

        self._send(op, seq, code, out or b"")
        now = time.ticks_ms()
        if follow is None:
            return
        kind = follow[0]
        if kind == "after":
            self._pending = {"kind": "after", "op": op, "seq": seq,
                             "due": time.ticks_add(now, follow[1]), "fn": follow[2]}
        elif kind == "poll" and code == IN_PROGRESS:
            self._pending = {"kind": "poll", "op": op, "seq": seq, "fn": follow[1],
                             "deadline": time.ticks_add(now, follow[2])}

    def tick(self):
        p = self._pending
        if p is None:
            return
        now = time.ticks_ms()
        if p["kind"] == "after":
            if time.ticks_diff(now, p["due"]) >= 0:
                self._pending = None
                try:
                    p["fn"]()
                except Exception as e:
                    print("[BLE] after-action err:", repr(e))
            return
        # poll
        try:
            r = p["fn"]()
        except Exception as e:
            print("[BLE] poll err:", repr(e))
            r = (ERR_INTERNAL, b"")
        if r is None and time.ticks_diff(now, p["deadline"]) >= 0:
            r = (ERR_INTERNAL, b"")
            print("[BLE] cmd 0x%02x timed out" % p["op"])
        if r is not None:
            self._pending = None
            self._send(p["op"], p["seq"], r[0], r[1] or b"")
