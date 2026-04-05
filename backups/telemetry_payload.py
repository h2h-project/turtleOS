# src/app/telemetry_payload.py — Build telemetry JSON payload (Pico / MicroPython safe)
#
# Centralizes the mapping from "reading + device state" -> payload dict.
# Dependency-light so it can be used from scheduler/net code.
#
# Updated for current API shape:
# - Builds:
#     recorded_at
#     values
#     confidence (optional)
#     flags (optional)
#     lat/lon/alt_m (optional)
#
# KEY FIX (Feb 2026):
# - Do NOT trust time.time()/time.gmtime() on RP2040 ports after RTC sync.
# - Prefer deriving unix seconds from machine.RTC().datetime() using time.mktime().
# - Only fall back to rtc dict unix keys or time.time() as last resort.

import time


def _safe_int(v, default=None):
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def _safe_float(v, default=None):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _get(d, key, default=None):
    try:
        if isinstance(d, dict) and key in d:
            return d.get(key)
    except Exception:
        pass
    return default


def _safe_mktime(dt8):
    """
    MicroPython mktime signature varies slightly by port.
    We try a couple of tuple shapes.

    Expected input:
      (year, month, mday, hour, minute, second, weekday0, yearday)
    weekday0: 0..6
    """
    try:
        return int(time.mktime(dt8))
    except Exception:
        pass

    try:
        y, mo, d, hh, mm, ss, wd, yd = dt8
        return int(time.mktime((int(y), int(mo), int(d), int(hh), int(mm), int(ss), 0, 0)))
    except Exception:
        return None


def _rtc_datetime_utc():
    """
    Read machine.RTC().datetime() safely.
    Returns (y,mo,d,wd0,hh,mm,ss) or None.
    """
    try:
        from machine import RTC
        dt = RTC().datetime()  # (y,mo,d,wd0,hh,mm,ss,subsec)
        if not isinstance(dt, tuple) or len(dt) < 7:
            return None
        y, mo, d, wd0, hh, mm, ss = dt[0], dt[1], dt[2], dt[3], dt[4], dt[5], dt[6]
        return (int(y), int(mo), int(d), int(wd0), int(hh), int(mm), int(ss))
    except Exception:
        return None


def _rtc_unix_s_from_machine_rtc():
    """
    Best source on RP2040 Pico builds:
      machine.RTC().datetime() -> time.mktime()
    Returns int unix seconds or None.
    """
    dt = _rtc_datetime_utc()
    if not dt:
        return None

    y, mo, d, wd0, hh, mm, ss = dt
    unix_s = _safe_mktime((y, mo, d, hh, mm, ss, wd0, 0))
    if unix_s is None:
        return None

    if int(unix_s) < 1000000000:
        return None

    return int(unix_s)


def _now_unix_s(rtc=None):
    """
    Prefer unix seconds derived from machine.RTC().datetime().
    Fall back to rtc dict unix keys if present.
    Finally fall back to time.time().
    """
    u = _rtc_unix_s_from_machine_rtc()
    if u is not None:
        return u

    for k in ("unix", "unix_s", "ts", "time_s"):
        v = _get(rtc, k)
        if v is not None:
            iv = _safe_int(v, None)
            if iv is not None and iv > 1000000000:
                return iv

    try:
        t = int(time.time())
        if t > 1000000000:
            return t
    except Exception:
        pass

    return None


def build_payload(reading=None, rtc=None, gps=None, cfg=None, device=None, extra=None):
    """
    Build a dict matching the current telemetry API shape.

    Parameters
    ----------
    reading : dict|object|None
        Expected to contain e.g. eco2_ppm, tvoc_ppb, temp_c, rh/rh_pct, aqi, confidence.
        If it's an object, attribute access is used as fallback.

    rtc : dict|None
        RTC info dict from boot sync (temp_c, synced, osf, etc.)

    gps : dict|None
        Optional GPS fix dict (lat, lon, alt_m, sats, hdop, fix, etc.)

    cfg : dict|None
        Optional config snapshot.

    device : dict|None
        Optional device metadata, currently unused in payload body but kept for future flags/meta derivation.

    extra : dict|None
        Optional extra fields. Only merged if they do not overwrite core keys.

    Returns
    -------
    dict
    """
    payload = {}

    # ----------------------------
    # recorded_at
    # ----------------------------
    payload["recorded_at"] = _now_unix_s(rtc=rtc)

    # ----------------------------
    # Reading (dict or object) -> values / confidence
    # ----------------------------
    r = {}
    if isinstance(reading, dict):
        r = reading
    elif reading is not None:
        r = {
            "eco2_ppm": getattr(reading, "eco2_ppm", None),
            "tvoc_ppb": getattr(reading, "tvoc_ppb", None),
            "temp_c": getattr(reading, "temp_c", None),
            "rh_pct": getattr(reading, "rh_pct", None),
            "rh": getattr(reading, "rh", None),
            "humidity": getattr(reading, "humidity", None),
            "aqi": getattr(reading, "aqi", None),
            "confidence": getattr(reading, "confidence", None),
            "ready": getattr(reading, "ready", None),
        }

    rh_val = _get(r, "rh_pct", None)
    if rh_val is None:
        rh_val = _get(r, "rh", None)
    if rh_val is None:
        rh_val = _get(r, "humidity", None)

    values = {
        "eco2_ppm": _safe_int(_get(r, "eco2_ppm"), None),
        "tvoc_ppb": _safe_int(_get(r, "tvoc_ppb"), None),
        "temp_c": _safe_float(_get(r, "temp_c"), None),
        "rh_pct": _safe_float(rh_val, None),
        "aqi": _safe_int(_get(r, "aqi"), None),
        "ready": bool(_get(r, "ready")) if _get(r, "ready") is not None else None,
        "rtc_temp_c": _safe_float(_get(rtc, "temp_c"), None),
    }

    # Remove None values to keep payload compact
    compact_values = {}
    try:
        for k, v in values.items():
            if v is not None:
                compact_values[k] = v
    except Exception:
        compact_values = values

    if not compact_values:
        compact_values["note"] = "no_reading"

    payload["values"] = compact_values

    conf = _safe_int(_get(r, "confidence"), None)
    if conf is not None:
        payload["confidence"] = {
            "sensor_confidence": conf
        }

    # ----------------------------
    # GPS (top-level lat/lon/alt_m)
    # ----------------------------
    if isinstance(gps, dict):
        lat = _safe_float(_get(gps, "lat"), None)
        lon = _safe_float(_get(gps, "lon"), None)
        alt_m = _safe_float(_get(gps, "alt_m"), None)

        if lat is not None:
            payload["lat"] = lat
        if lon is not None:
            payload["lon"] = lon
        if alt_m is not None:
            payload["alt_m"] = alt_m

        # Optional GPS-related flags
        gps_fix = _safe_int(_get(gps, "fix"), None)
        gps_sats = _safe_int(_get(gps, "sats"), None)
    else:
        gps_fix = None
        gps_sats = None

    # ----------------------------
    # flags (optional)
    # ----------------------------
    flags = {}

    # RTC snapshot hints
    if isinstance(rtc, dict):
        synced = _get(rtc, "synced", None)
        osf = _get(rtc, "osf", None)

        if synced is not None:
            flags["rtc_synced"] = bool(synced)
        if osf is not None:
            flags["rtc_osf"] = bool(osf)

    # Config hints
    if isinstance(cfg, dict):
        flags["telemetry_enabled"] = bool(cfg.get("telemetry_enabled", True))
        if cfg.get("gps_enabled") is not None:
            flags["gps_enabled"] = bool(cfg.get("gps_enabled"))

    # GPS hints
    if gps_fix is not None:
        flags["gps_fix"] = gps_fix
    if gps_sats is not None:
        flags["gps_sats"] = gps_sats

    # Device/meta hints only if explicitly useful
    if isinstance(device, dict):
        try:
            if device.get("device_id") is not None:
                flags["device_id_present"] = True
        except Exception:
            pass

    if flags:
        payload["flags"] = flags

    # ----------------------------
    # Merge extra (optional)
    # ----------------------------
    if isinstance(extra, dict):
        try:
            for k, v in extra.items():
                if k not in payload:
                    payload[k] = v
        except Exception:
            pass

    return payload