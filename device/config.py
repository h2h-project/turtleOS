# config.py — AirBuddy 2.1 device configuration manager
#
# - Stores configuration in config.json on flash
# - Applies defaults
# - Normalizes types
# - Migrates legacy keys (api-base -> api_base)
# - Writes safely using temp file swap
# - Leaves deployment-specific values blank so user sets them in config.json

import json
import os

CONFIG_FILE = "config.json"

# ----------------------------
# Defaults
# ----------------------------
DEFAULTS = {
    # --- Board identity ---
    # Set to a known tag to override automatic detection in platform.py.
    # Valid values: "pico", "esp32", "esp32s3", "xiao_esp32s3"
    # Empty string = auto-detect from uos.uname().machine.
    "board_type": "",

    # --- Hardware ---
    "gps_enabled": True,
    # "l76k" = Seeed GNSS Add-on for XIAO (Quectel L76K, PMTK commands) — default
    # "neo6m" = u-blox NEO-6M (UBX binary) — LEGACY-NEO6M: remove when retired
    "gps_module": "l76k",
    "oled_col_offset": 0,  # 0 = large/SSD1306, 2 = small 1.3" SH1106

    # --- WiFi ---
    "wifi_enabled": True,
    "wifi_ssid": "",
    "wifi_password": "",

    # --- Mission link ---
    # How the device tries to SHIP stored records. Orthogonal to
    # telemetry_mode, which governs when records are CREATED. Readings always
    # accumulate in telemetry_queue.json regardless; this only decides when a
    # radio is allowed to come up.
    #   "wifi_auto"   = attempt association on an exponential backoff
    #                   (15 min, doubling to a 12 h cap; resets on success)
    #   "wifi_manual" = never attempt on its own. Only a deliberate
    #                   triple-click into the connectivity carousel connects.
    #                   The right setting for a launched turtle: there is no
    #                   home AP at sea, so every automatic scan is wasted TX.
    #   "lora"        = reserved, not yet implemented
    "mission_connection_mode": "wifi_auto",

    # --- Telemetry ---
    # telemetry_mode is authoritative:
    #   "auto"   = post automatically every telemetry_post_every_s seconds
    #   "manual" = no automatic posting; only stamps taken by hand on the GPS screen
    #   "off"    = never post
    # telemetry_enabled is a derived mirror (mode != "off") kept so older readers
    # keep working. Writers must set telemetry_mode — _normalize_types() rebuilds
    # telemetry_enabled from it on every load.
    "telemetry_mode": "auto",
    "telemetry_enabled": True,
    "telemetry_post_every_s": 120,
    "morse_bless": False,   # blink button LED in Morse before each send; only useful when LED is wired

    # --- Device Identity / API ---
    # Intentionally blank: user must set these in config.json
    "api_base": "",
    "device_id": "",
    "device_key": "",
    # Human-readable turtle name (turtles_tb.name). Written by step_api() on
    # every online boot from GET /v1/device's "device_name", so an offline boot
    # still has a name to show and to advertise over Bluetooth.
    "device_name": "",

    # --- Bluetooth (Turtle Wrangler, docs/wrangler/) ---
    # ble_enabled: master switch. False = the radio is never activated — the
    #   setting for a real mission voyage.
    # ble_window_min: after boot, wake, and each visit to the Bluetooth screen
    #   the turtle advertises for this many minutes. 0 = always while enabled.
    # ble_require_bond: commands need a bonded, encrypted link. False is for
    #   bench development only; never on a deployed turtle.
    "ble_enabled": True,
    "ble_window_min": 10,
    "ble_require_bond": True,

    # --- Time ---
    # Minutes offset from UTC.
    # Example: Jakarta = 420
    # None means not configured (Time screen will show NO:TZ)
    "timezone_offset_min": None,

    # --- Compass calibration ---
    # Applied by src/nav/heading.py to the raw magnetometer heading (the
    # GY-87's on-board QMC5883L/HMC5883L, or a standalone GY-271 fallback).
    # Degrees to add to raw heading so that the reading matches true North.
    # Example: if raw=80 when pointing North, set compass_offset_deg to -80.
    "compass_offset_deg": 0,

    # --- Turtle mode ---
    # When true, replaces "airOS" with "turtleOS" on the boot screen.
    "turtle_mode": True,

    # --- Joke mode ---
    # When true, quad-click shows the self-destruct screen (scary).
    # When false, quad-click shows the turtle waiting animation (chill).
    "joke_mode": False,

    # --- Servo ---
    # Set to true only when the MG996R servo is physically wired to D8/GPIO7.
    # PWM init always succeeds regardless of physical connection, so this flag
    # is the only reliable way to know if a servo is actually present.
    "servo_present": False,

    # --- Mission destination (turtle mode) ---
    # The grand-mission target, assigned on hopeturtles.org and pushed to the
    # device by the Device API boot step (GET /v1/device -> mission_target_*).
    # mission_destination: [lat_float, lon_float] in decimal degrees.
    "mission_dest_full_name": "Al Mawasi, Gaza",
    "mission_dest_short_name": "",
    "mission_destination": [31.35, 34.27],
    # mission_waypoints: ordered mission list of [lat, lon] pairs; when empty
    # the sequencer falls back to a single-waypoint mission at
    # mission_destination.
    "mission_waypoints": [],

    # --- Operator-set test project (turtle mode) ---
    # Set by hand on the Destination screen for pond/field tests, independent
    # of the grand mission above. WaypointSequencer prefers these when set.
    # Each *_destination/departure/arrival is [lat, lon] or None (not set).
    # Mirrored to turtles_tb via PATCH /v1/device for the dashboard.
    "set_destination": None,
    "set_departure": None,
    "set_arrival": None,
    "set_waypoints": [],
    "set_short_name": "",
    "set_full_name": "",

    # --- Navigation (turtle mode, src/nav/) ---
    "nav_cycle_ms": 300,          # autopilot cycle (PDF spec: 200-500 ms)
    "arrival_radius_m": 300,      # waypoint arrival radius
    "luff_sweep_dps": 8,          # sweep speed, deg/s (PDF spec: 5-10)
    "luff_threshold_mult": 5.0,   # luff variance threshold vs calm baseline
    "luff_resweep_s": 600,        # periodic wind re-sweep in SAIL-NAV (PDF: ~10 min)
    "sail_min_deg": 10,           # servo physical stop (lower)
    "sail_max_deg": 170,          # servo physical stop (upper)
    "low_batt_pct": 20,           # below this, feather the sail (PDF failsafe)
    "gps_loss_safe_s": 120,       # no GPS fix this long in SAIL-NAV -> SAFE
}

LEGACY_KEYS_TO_REMOVE = (
    "api-base",
)

# Valid telemetry_mode values, in toggle-cycle order (off -> auto -> manual -> off).
TELEMETRY_MODES = ("off", "auto", "manual")


# ----------------------------
# Public API
# ----------------------------
def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}

    changed = False

    # --- Migrate legacy keys ---
    if "api_base" not in cfg and "api-base" in cfg:
        cfg["api_base"] = cfg.get("api-base")
        changed = True

    # dest_* / waypoints -> mission_* (clarity: mission_ = grand mission,
    # set_ = operator test project). Copy the value across, then drop the
    # old key so it does not linger in config.json.
    for _old, _new in (
        ("dest_name", "mission_dest_full_name"),
        ("dest_coord", "mission_destination"),
        ("waypoints", "mission_waypoints"),
    ):
        if _old in cfg:
            if _new not in cfg:
                cfg[_new] = cfg.get(_old)
            try:
                del cfg[_old]
            except Exception:
                pass
            changed = True

    for k in LEGACY_KEYS_TO_REMOVE:
        if k in cfg:
            try:
                del cfg[k]
            except Exception:
                pass
            changed = True

    # --- Migrate legacy telemetry boolean -> telemetry_mode ---
    # Must run before defaults are applied, while we can still tell whether the
    # file predates telemetry_mode. A device that had telemetry off stays off.
    if "telemetry_mode" not in cfg:
        cfg["telemetry_mode"] = "auto" if _to_bool(
            cfg.get("telemetry_enabled", DEFAULTS["telemetry_enabled"]),
            DEFAULTS["telemetry_enabled"],
        ) else "off"
        changed = True

    # --- Apply defaults ---
    for k, v in DEFAULTS.items():
        if k not in cfg:
            cfg[k] = v
            changed = True

    # --- Normalize ---
    cfg, normalized_changed = _normalize_types(cfg)
    changed = changed or normalized_changed

    if changed or not file_exists(CONFIG_FILE):
        save_config(cfg)

    return cfg


def save_config(cfg):
    tmp_file = CONFIG_FILE + ".tmp"

    with open(tmp_file, "w") as f:
        json.dump(cfg, f)

    try:
        os.remove(CONFIG_FILE)
    except Exception:
        pass

    os.rename(tmp_file, CONFIG_FILE)


def update_config(changes):
    """Merge `changes` into the config on flash and return the saved config.

    Reads the file fresh, merges, normalizes and writes it back in one step.
    Every writer should use this instead of saving a cfg dict it loaded
    earlier: a screen that holds a stale copy and calls save_config() on it
    silently reverts anything written in the meantime (a Bluetooth command,
    the boot API sync, another screen).
    """
    cfg = load_config()
    cfg.update(changes)
    cfg, _ = _normalize_types(cfg)
    save_config(cfg)
    return cfg


def file_exists(path):
    try:
        os.stat(path)
        return True
    except Exception:
        return False


# ----------------------------
# Helpers
# ----------------------------
def _to_bool(val, default=False):
    if isinstance(val, bool):
        return val

    if isinstance(val, int):
        return val != 0

    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off", ""):
            return False

    return default


# ----------------------------
# Normalization
# ----------------------------
def _normalize_types(cfg):
    changed = False

    # --- OLED column offset ---
    try:
        col_offset = int(cfg.get("oled_col_offset", DEFAULTS["oled_col_offset"]))
        if col_offset < 0 or col_offset > 4:
            col_offset = DEFAULTS["oled_col_offset"]
    except Exception:
        col_offset = DEFAULTS["oled_col_offset"]
        changed = True
    if cfg.get("oled_col_offset") != col_offset:
        cfg["oled_col_offset"] = col_offset
        changed = True

    # --- Booleans ---
    for key in ("gps_enabled", "wifi_enabled", "telemetry_enabled", "turtle_mode", "joke_mode", "servo_present",
                "ble_enabled", "ble_require_bond"):
        old_val = cfg.get(key, DEFAULTS[key])
        new_val = _to_bool(old_val, DEFAULTS[key])
        if old_val != new_val:
            cfg[key] = new_val
            changed = True

    # --- Telemetry mode (tri-state; authoritative) ---
    # Runs after the boolean loop above so the derived mirror always wins.
    try:
        mode = str(cfg.get("telemetry_mode", DEFAULTS["telemetry_mode"]) or "").strip().lower()
    except Exception:
        mode = DEFAULTS["telemetry_mode"]
    if mode not in TELEMETRY_MODES:
        mode = DEFAULTS["telemetry_mode"]
    if cfg.get("telemetry_mode") != mode:
        cfg["telemetry_mode"] = mode
        changed = True

    tel_enabled = (mode != "off")
    if cfg.get("telemetry_enabled") != tel_enabled:
        cfg["telemetry_enabled"] = tel_enabled
        changed = True

    # --- Strings ---
    wifi_ssid = str(cfg.get("wifi_ssid", "") or "").strip()
    wifi_password = str(cfg.get("wifi_password", "") or "").strip()
    api_base = str(cfg.get("api_base", "") or "").strip().rstrip("/")
    device_id = str(cfg.get("device_id", "") or "").strip()
    device_key = str(cfg.get("device_key", "") or "").strip()

    # Accept legacy https value if user put it there, but do not force a fallback.
    # If your stack later supports TLS reliably, this should remain untouched.
    # For now we preserve exactly what the user entered except trimming trailing slash.

    # --- Telemetry interval ---
    try:
        interval = int(cfg.get("telemetry_post_every_s", DEFAULTS["telemetry_post_every_s"]))
    except Exception:
        interval = DEFAULTS["telemetry_post_every_s"]
        changed = True

    if interval < 10:
        interval = 10
        changed = True

    # --- Timezone offset (minutes) ---
    tz = cfg.get("timezone_offset_min", None)

    if tz is None or tz == "":
        if cfg.get("timezone_offset_min", None) is not None:
            cfg["timezone_offset_min"] = None
            changed = True
    else:
        try:
            tz = int(tz)
            if -720 <= tz <= 840:  # UTC-12 to UTC+14
                if cfg.get("timezone_offset_min") != tz:
                    cfg["timezone_offset_min"] = tz
                    changed = True
            else:
                cfg["timezone_offset_min"] = None
                changed = True
        except Exception:
            cfg["timezone_offset_min"] = None
            changed = True

    # --- Write back normalized values ---
    if cfg.get("telemetry_post_every_s") != interval:
        cfg["telemetry_post_every_s"] = interval
        changed = True

    if cfg.get("wifi_ssid") != wifi_ssid:
        cfg["wifi_ssid"] = wifi_ssid
        changed = True

    if cfg.get("wifi_password") != wifi_password:
        cfg["wifi_password"] = wifi_password
        changed = True

    if cfg.get("api_base") != api_base:
        cfg["api_base"] = api_base
        changed = True

    if cfg.get("device_id") != device_id:
        cfg["device_id"] = device_id
        changed = True

    if cfg.get("device_key") != device_key:
        cfg["device_key"] = device_key
        changed = True

    # --- Compass offset ---
    try:
        compass_offset = int(cfg.get("compass_offset_deg", DEFAULTS["compass_offset_deg"]))
        compass_offset = max(-360, min(360, compass_offset))
    except Exception:
        compass_offset = DEFAULTS["compass_offset_deg"]
        changed = True
    if cfg.get("compass_offset_deg") != compass_offset:
        cfg["compass_offset_deg"] = compass_offset
        changed = True

    # --- Destination name strings (mission + operator-set) ---
    for _key in ("mission_dest_full_name", "mission_dest_short_name",
                 "set_short_name", "set_full_name"):
        _s = str(cfg.get(_key, DEFAULTS[_key]) or "").strip()
        if cfg.get(_key) != _s:
            cfg[_key] = _s
            changed = True

    # --- Destination coordinates (mission + operator-set) ---
    # A point is [lat, lon] with lat in +/-90, lon in +/-180. mission_
    # destination falls back to the default; the set_ points fall back to
    # None (meaning "not set"), so an invalid value can't masquerade as a
    # target.
    def _valid_pair(val):
        if isinstance(val, (list, tuple)) and len(val) == 2:
            try:
                _la = float(val[0]); _lo = float(val[1])
                if -90.0 <= _la <= 90.0 and -180.0 <= _lo <= 180.0:
                    return [_la, _lo]
            except Exception:
                pass
        return None

    _md = _valid_pair(cfg.get("mission_destination", DEFAULTS["mission_destination"]))
    if _md is None:
        _md = list(DEFAULTS["mission_destination"])
    if cfg.get("mission_destination") != _md:
        cfg["mission_destination"] = _md
        changed = True

    for _key in ("set_destination", "set_departure", "set_arrival"):
        _p = _valid_pair(cfg.get(_key))
        if cfg.get(_key) != _p:
            cfg[_key] = _p
            changed = True

    # --- Waypoint lists (mission + operator-set) ---
    # Keep only well-formed [lat, lon] pairs; a malformed list degrades to
    # the *_destination fallback inside WaypointSequencer rather than
    # crashing.
    for _key in ("mission_waypoints", "set_waypoints"):
        _wps = cfg.get(_key, DEFAULTS[_key])
        if not isinstance(_wps, list):
            if cfg.get(_key) != []:
                cfg[_key] = []
                changed = True
            continue
        _clean = []
        for _wp in _wps:
            _cp = _valid_pair(_wp)
            if _cp is not None:
                _clean.append(_cp)
        if _clean != _wps:
            cfg[_key] = _clean
            changed = True

    # --- Bluetooth wrangle window (minutes; 0 = always advertise) ---
    try:
        _bw = int(cfg.get("ble_window_min", DEFAULTS["ble_window_min"]))
        _bw = max(0, min(240, _bw))
    except Exception:
        _bw = DEFAULTS["ble_window_min"]
    if cfg.get("ble_window_min") != _bw:
        cfg["ble_window_min"] = _bw
        changed = True

    _dn = str(cfg.get("device_name", "") or "").strip()
    if cfg.get("device_name") != _dn:
        cfg["device_name"] = _dn
        changed = True

    # --- Mission link mode ---
    try:
        _mcm = str(cfg.get("mission_connection_mode", "wifi_auto") or "").strip().lower()
    except Exception:
        _mcm = ""
    if _mcm not in ("wifi_auto", "wifi_manual", "lora"):
        _mcm = "wifi_auto"
    if cfg.get("mission_connection_mode") != _mcm:
        cfg["mission_connection_mode"] = _mcm
        changed = True

    # --- Nav numeric tunables ---
    for key, lo, hi in (
        ("nav_cycle_ms", 100, 2000),
        ("arrival_radius_m", 20, 5000),
        ("luff_sweep_dps", 2, 30),
        ("luff_resweep_s", 60, 3600),
        ("sail_min_deg", 0, 90),
        ("sail_max_deg", 90, 180),
        ("low_batt_pct", 0, 90),
        ("gps_loss_safe_s", 10, 3600),
    ):
        try:
            v = int(cfg.get(key, DEFAULTS[key]))
            v = max(lo, min(hi, v))
        except Exception:
            v = DEFAULTS[key]
        if cfg.get(key) != v:
            cfg[key] = v
            changed = True

    try:
        mult = float(cfg.get("luff_threshold_mult", DEFAULTS["luff_threshold_mult"]))
        mult = max(1.5, min(20.0, mult))
    except Exception:
        mult = DEFAULTS["luff_threshold_mult"]
    if cfg.get("luff_threshold_mult") != mult:
        cfg["luff_threshold_mult"] = mult
        changed = True

    return cfg, changed