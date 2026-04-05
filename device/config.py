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
    # --- Hardware ---
    "gps_enabled": True,
    "oled_col_offset": 0,  # 0 = large/SSD1306, 2 = small 1.3" SH1106

    # --- WiFi ---
    "wifi_enabled": True,
    "wifi_ssid": "",
    "wifi_password": "",

    # --- Telemetry ---
    "telemetry_enabled": True,
    "telemetry_post_every_s": 120,

    # --- Device Identity / API ---
    # Intentionally blank: user must set these in config.json
    "api_base": "",
    "device_id": "",
    "device_key": "",

    # --- Time ---
    # Minutes offset from UTC.
    # Example: Jakarta = 420
    # None means not configured (Time screen will show NO:TZ)
    "timezone_offset_min": None,
}

LEGACY_KEYS_TO_REMOVE = (
    "api-base",
)


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

    for k in LEGACY_KEYS_TO_REMOVE:
        if k in cfg:
            try:
                del cfg[k]
            except Exception:
                pass
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
    for key in ("gps_enabled", "wifi_enabled", "telemetry_enabled"):
        old_val = cfg.get(key, DEFAULTS[key])
        new_val = _to_bool(old_val, DEFAULTS[key])
        if old_val != new_val:
            cfg[key] = new_val
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

    return cfg, changed