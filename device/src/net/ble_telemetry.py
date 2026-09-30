# src/net/ble_telemetry.py — pack Turtle service characteristics (Phase 3)
#
# Turns runtime state the firmware already holds into the byte layouts of
# GATT contract v1 (docs/wrangler/README.md -> Turtle service). Nothing here
# computes new data except bearing-to-target; nothing here touches the radio
# — ble_service.py writes/notifies what this returns.
#
# Rules from the contract:
#   - little-endian, packed, struct "<" formats exactly as documented;
#   - every field that can be missing uses its sentinel, never 0;
#   - each characteristic has its own refresh period and only notifies when
#     its bytes change (Shore sync also forces one per minute for the clock).
#
# Sources are getter callables handed in by run() (see make_sources()), so a
# screen or object rebuilt later is always the current one.

import struct
import time

# ---- sentinels ----
NA_I32 = 0x7FFFFFFF
NA_U16 = 0xFFFF
NA_I16 = 0x7FFF
NA_U32 = 0xFFFFFFFF
NA_U8 = 0xFF

# ---- characteristic short IDs ----
POSITION = 0x0110
NAV = 0x0111
TARGETS = 0x0112
SAIL = 0x0113
POWER = 0x0114
IMU = 0x0115
STATUS = 0x0116
SHORE = 0x0117

# (short id, refresh ms, force-notify ms or 0)
SCHEDULE = (
    (POSITION, 1000, 0),
    (NAV, 1000, 0),
    (TARGETS, 2000, 0),
    (SAIL, 1000, 0),      # 500 ms while a sweep runs (see period())
    (POWER, 5000, 0),
    (IMU, 1000, 0),
    (STATUS, 2000, 0),
    (SHORE, 5000, 60000), # device_now ticks every second; notify it 1/min
)

_NAV_STATES = {"BOOT": 0, "ACQUIRE": 1, "SAIL_NAV": 2, "ARRIVAL": 3, "SAFE": 4}
_FAULTS = {None: 0, "": 0, "gps never acquired": 1, "gps loss": 2,
           "no waypoints configured": 3}
_TRIM = {"CRUISE": 0, "FEATHER": 1}

# Status flag bits
F_WIFI_CONNECTED = 1 << 0
F_API_OK = 1 << 1
F_GPS_FIXED = 1 << 2
F_JOURNEY_ACTIVE = 1 << 3
F_WIFI_ENABLED = 1 << 4
F_GPS_ENABLED = 1 << 5
F_GPS_HW = 1 << 6
F_IMU = 1 << 7
F_MAG = 1 << 8
F_INA219 = 1 << 9
F_BARO = 1 << 10
F_AS5600 = 1 << 11
F_SERVO = 1 << 12
F_RTC_SYNCED = 1 << 13
F_LINK_BONDED = 1 << 14
F_REQUIRE_BOND = 1 << 15
F_CMD_IN_PROGRESS = 1 << 16
F_WIFI_CREDS = 1 << 17

_TELEMETRY_MODES = {"off": 0, "auto": 1, "manual": 2}
_CONN_MODES = {"wifi_auto": 0, "wifi_manual": 1, "lora": 2}

FIX_FRESH_MS = 10_000   # a cached fix older than this is not "fixed"


# ---------------------------------------------------------------- helpers

def _e7(deg):
    if deg is None:
        return NA_I32
    try:
        return int(round(float(deg) * 1e7))
    except Exception:
        return NA_I32


def _x10_u16(v, mod360=False):
    if v is None:
        return NA_U16
    try:
        f = float(v)
        if mod360:
            f %= 360.0
        n = int(round(f * 10))
        return n if 0 <= n < NA_U16 else NA_U16
    except Exception:
        return NA_U16


def _x10_i16(v):
    if v is None:
        return NA_I16
    try:
        n = int(round(float(v) * 10))
        return n if -32768 <= n < NA_I16 else NA_I16
    except Exception:
        return NA_I16


def _u8(v):
    if v is None:
        return NA_U8
    try:
        n = int(v)
        return n if 0 <= n < NA_U8 else NA_U8
    except Exception:
        return NA_U8


def _pair(val):
    if isinstance(val, (list, tuple)) and len(val) == 2:
        try:
            return float(val[0]), float(val[1])
        except Exception:
            pass
    return None


def _call(getter):
    try:
        return getter() if getter is not None else None
    except Exception:
        return None


def rtc_unix():
    """Unix seconds from the RTC, or 0 when not synced (matches the
    telemetry scheduler's epoch rule)."""
    try:
        from machine import RTC
        y, mo, d, _wd, hh, mm, ss, _sub = RTC().datetime()
        if y < 2020:
            return 0
        # Same epoch-independent conversion the scheduler uses — time.mktime()
        # counts from 2000 on ESP32 but 1970 elsewhere.
        from src.app.telemetry_scheduler import TelemetryScheduler
        return int(TelemetryScheduler._dt_to_unix(y, mo, d, hh, mm, ss))
    except Exception:
        return 0


# ---------------------------------------------------------------- packer

class TurtleData:
    """Packs each characteristic from getter callables:

        nav()        NavController or None
        ina()        INA219 or None
        imu()        GY87 or None
        gps()        GnssModule or None
        status()     run()'s status dict (wifi_ok, api_ok, ...)
        cfg()        live config dict
        telemetry()  TelemetryState or None
        bonded()     True when the current link is encrypted + bonded (Phase 4)
        cmd_busy()   True while a command runs (Phase 4)
    """

    def __init__(self, sources):
        self._s = sources or {}
        # file-backed values, refreshed slowly (they read flash)
        self._queue = 0
        self._last_ok_ts = 0
        self._next_file_ms = 0

    def _g(self, key):
        return _call(self._s.get(key))

    # -- slow, flash-backed values --------------------------------------
    def _refresh_files(self, now):
        if time.ticks_diff(now, self._next_file_ms) < 0:
            return
        self._next_file_ms = time.ticks_add(now, 10_000)
        try:
            from src.app.telemetry_state import TelemetryState
            self._queue = int(TelemetryState.get_queue_size() or 0)
        except Exception:
            pass
        try:
            from src.app.telemetry_scheduler import TelemetryScheduler
            last = TelemetryScheduler.read_last_sent()
            if isinstance(last, dict) and last.get("ok") and last.get("ts"):
                self._last_ok_ts = int(last["ts"])
        except Exception:
            pass

    # -- characteristics --------------------------------------------------
    def position(self):
        from src.nav import gpsfix
        lat, lon, age = gpsfix.get()
        q, sats, _gage = gpsfix.gga()
        age_s = NA_U16 if age is None else min(int(age) // 1000, NA_U16 - 1)
        return struct.pack("<iiBBH", _e7(lat), _e7(lon), _u8(sats), _u8(q), age_s)

    def nav(self):
        nav = self._g("nav")
        heading = bearing = xte = None
        dist = NA_U32
        state = fault = trim = NA_U8
        if nav is not None:
            try:
                snap = nav.snapshot()
            except Exception:
                snap = {}
            heading = snap.get("heading")
            state = _NAV_STATES.get(snap.get("state"), NA_U8)
            fault = _FAULTS.get(snap.get("fault"), NA_U8) if state == 4 else 0
            trim = _TRIM.get(snap.get("trim"), NA_U8)
            d = snap.get("wp_dist_m")
            if d is not None:
                dist = min(int(d), NA_U32 - 1)
            try:
                from src.nav import gpsfix
                from src.nav.bearing import initial_bearing
                lat, lon, _age = gpsfix.get()
                wp = nav.target()
                if lat is not None and wp is not None:
                    bearing = initial_bearing(lat, lon, wp[0], wp[1])
            except Exception:
                pass
        # xte: no cross-track function yet -> sentinel (contract v1 note)
        return struct.pack("<HHIhBBB", _x10_u16(heading, True), _x10_u16(bearing, True),
                           dist, NA_I16 if xte is None else _x10_i16(xte), state, fault, trim)

    def targets(self):
        cfg = self._g("cfg") or {}
        sd = _pair(cfg.get("set_destination"))
        md = _pair(cfg.get("mission_destination"))
        src = 0
        nav = self._g("nav")
        if nav is not None:
            try:
                src = int(nav.route_source())
            except Exception:
                src = 0
        return struct.pack("<iiiiB",
                           _e7(sd[0] if sd else None), _e7(sd[1] if sd else None),
                           _e7(md[0] if md else None), _e7(md[1] if md else None),
                           _u8(src))

    def sail(self):
        nav = self._g("nav")
        sail = wind = None
        sweep = 0
        if nav is not None:
            try:
                snap = nav.snapshot()
                sail = snap.get("sail")
                wind = snap.get("wind")
                sweep = 1 if snap.get("sweeping") else 0
            except Exception:
                pass
        # confidence comes from the bench sweep (Phase 6); the nav sweep has none
        return struct.pack("<HHBB", _x10_u16(sail, True), _x10_u16(wind, True),
                           sweep, NA_U8)

    def sweeping(self):
        nav = self._g("nav")
        try:
            return bool(nav is not None and nav.sweeping())
        except Exception:
            return False

    def power(self):
        ina = self._g("ina")
        mv, ma = NA_U16, NA_I16
        if ina is not None:
            try:
                v = ina.bus_voltage_v()
                if v is not None:
                    mv = min(int(round(float(v) * 1000)), NA_U16 - 1)
            except Exception:
                pass
            try:
                c = ina.current_ma()
                if c is not None:
                    ma = max(-32768, min(int(round(float(c))), NA_I16 - 1))
            except Exception:
                pass
        soc = None
        nav = self._g("nav")
        if nav is not None:
            try:
                soc = nav.battery_pct()
            except Exception:
                soc = None
        return struct.pack("<HhB", mv, ma, _u8(soc))

    def imu(self):
        imu = self._g("imu")
        pitch = roll = press = None
        if imu is not None:
            if getattr(imu, "is_present", False):
                try:
                    pr = imu.pitch_roll()
                    if pr is not None:
                        pitch, roll = pr
                except Exception:
                    pass
            if getattr(imu, "baro", None) is not None:
                try:
                    r = imu.baro_read()
                    if r is not None and r[1] is not None and float(r[1]) > 0:
                        press = float(r[1])
                except Exception:
                    pass
        return struct.pack("<hhH", _x10_i16(pitch), _x10_i16(roll), _x10_u16(press))

    def status(self):
        cfg = self._g("cfg") or {}
        st = self._g("status") or {}
        nav = self._g("nav")
        imu = self._g("imu")
        f = 0
        if st.get("wifi_ok"):
            f |= F_WIFI_CONNECTED
        if st.get("api_ok"):
            f |= F_API_OK
        try:
            from src.nav import gpsfix
            _la, _lo, age = gpsfix.get()
            if age is not None and age < FIX_FRESH_MS:
                f |= F_GPS_FIXED
        except Exception:
            pass
        try:
            from src.app import journey
            if journey.active():
                f |= F_JOURNEY_ACTIVE
        except Exception:
            pass
        if cfg.get("wifi_enabled"):
            f |= F_WIFI_ENABLED
        if cfg.get("gps_enabled"):
            f |= F_GPS_ENABLED
        if self._g("gps") is not None:
            f |= F_GPS_HW
        if imu is not None:
            if getattr(imu, "is_present", False):
                f |= F_IMU
            if getattr(imu, "mag", None) is not None:
                f |= F_MAG
            if getattr(imu, "baro", None) is not None:
                f |= F_BARO
        if self._g("ina") is not None:
            f |= F_INA219
        try:
            if nav is not None and nav.sail_encoder_present():
                f |= F_AS5600
        except Exception:
            pass
        if cfg.get("servo_present"):
            f |= F_SERVO
        if rtc_unix():
            f |= F_RTC_SYNCED
        if self._g("bonded"):
            f |= F_LINK_BONDED
        if cfg.get("ble_require_bond", True):
            f |= F_REQUIRE_BOND
        if self._g("cmd_busy"):
            f |= F_CMD_IN_PROGRESS
        if str(cfg.get("wifi_ssid") or "").strip():
            f |= F_WIFI_CREDS

        mode = _TELEMETRY_MODES.get(str(cfg.get("telemetry_mode", "")).lower(), NA_U8)
        conn = _CONN_MODES.get(str(cfg.get("mission_connection_mode", "")).lower(), NA_U8)
        try:
            interval = min(int(cfg.get("telemetry_post_every_s", 120)), NA_U16 - 1)
        except Exception:
            interval = NA_U16
        try:
            from src.app.actions import stamps_session
            stamps = min(int(stamps_session()), NA_U16 - 1)
        except Exception:
            stamps = 0
        return struct.pack("<IBBHHH", f, mode, conn, interval,
                           min(self._queue, NA_U16 - 1), stamps)

    def shore(self):
        jid = 0
        try:
            from src.app import journey
            jid = int(journey.active_id() or 0)
        except Exception:
            jid = 0
        return struct.pack("<III", self._last_ok_ts & 0xFFFFFFFF, jid & 0xFFFFFFFF,
                           rtc_unix() & 0xFFFFFFFF)

    # -- dispatch -----------------------------------------------------------
    def pack(self, cid, now):
        self._refresh_files(now)
        if cid == POSITION:
            return self.position()
        if cid == NAV:
            return self.nav()
        if cid == TARGETS:
            return self.targets()
        if cid == SAIL:
            return self.sail()
        if cid == POWER:
            return self.power()
        if cid == IMU:
            return self.imu()
        if cid == STATUS:
            return self.status()
        if cid == SHORE:
            return self.shore()
        return None

    def period(self, cid, base_ms):
        """Refresh period; Sail speeds up to 2 Hz while a sweep runs."""
        if cid == SAIL and self.sweeping():
            return 500
        return base_ms


def notify_key(cid, value):
    """The part of a value whose change should trigger a notification.
    Shore sync ignores its clock field (last 4 bytes) — that one goes out on
    the once-a-minute forced notify instead."""
    if cid == SHORE and value is not None:
        return value[:-4]
    return value
