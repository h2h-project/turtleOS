# src/app/rtc_sync.py
#
# DS3231 sync + periodic temperature refresh (Pico-safe)
#
# Key idea:
# - Time sync is a one-time (boot) thing.
# - Temperature should be refreshed periodically, but DS3231 updates internally
#   about every ~64s anyway. We cache reads to avoid I2C spam.

import time
from machine import RTC
from src.drivers.ds3231 import DS3231


DS3231_ADDR = 0x68

# DS3231 internal temp conversion is ~64s typical.
# Use a little buffer so we don't thrash I2C.
TEMP_REFRESH_MS_DEFAULT = 70 * 1000


def _now_ms():
    try:
        return time.ticks_ms()
    except Exception:
        return int(time.time() * 1000)


def _ticks_diff(a, b):
    try:
        return time.ticks_diff(a, b)
    except Exception:
        return a - b


def ds3231_seconds_ticking(ds, sample_ms=1200):
    """
    Quick sanity check: seconds should change over ~1.2s.
    """
    try:
        t1 = ds.datetime()
        s1 = int(t1[6])
        time.sleep_ms(int(sample_ms))
        t2 = ds.datetime()
        s2 = int(t2[6])
        return s2 != s1
    except Exception:
        return False


def _safe_mktime(dt8):
    """
    MicroPython time.mktime() quirks vary by port.
    Try a couple formats safely and return epoch seconds or None.
    """
    try:
        return int(time.mktime(dt8))
    except Exception:
        pass
    try:
        y, mo, d, hh, mm, ss, wd, yd = dt8
        return int(time.mktime((y, mo, d, hh, mm, ss, 0, 0)))
    except Exception:
        return None


def _normalize_year(y):
    """
    DS3231 drivers sometimes return 0..99 for year; normalize to 2000+.
    If year already looks like 2026, keep it.
    """
    try:
        y = int(y)
    except Exception:
        return None

    if y < 100:
        return 2000 + y

    if 1970 <= y <= 2099:
        return y

    return None


def _normalize_wday(wd):
    """
    Normalize weekday to 0..6 where 0=Mon (MicroPython RTC convention).
    DS3231 libs vary (0..6, 1..7, Sunday-based, etc). We just coerce safely.
    """
    try:
        wd = int(wd)
    except Exception:
        return 0

    if 1 <= wd <= 7:
        return wd - 1

    if 0 <= wd <= 6:
        return wd

    return 0


def _read_temp_c(ds):
    """
    Best-effort temperature read from DS3231 instance.
    """
    for fn_name in ("temperature", "get_temperature", "temp", "read_temperature"):
        try:
            fn = getattr(ds, fn_name, None)
            if callable(fn):
                return float(fn())
        except Exception:
            pass
    return None


def _ds3231_detected(i2c):
    """
    Best-effort presence check.
    Returns True if addr appears in scan OR if scan isn't available (unknown).
    """
    if i2c is None:
        return False
    try:
        addrs = i2c.scan() or []
        return (DS3231_ADDR in addrs)
    except Exception:
        # Can't scan; unknown. We'll attempt to instantiate and catch.
        return True


def refresh_ds3231_temp(i2c, rtc_info, refresh_ms=TEMP_REFRESH_MS_DEFAULT, force=False):
    """
    Periodically refresh DS3231 temperature into rtc_info dict.

    rtc_info keys used/updated:
      detected: bool (best effort)
      temp_c: float|None
      temp_ok: bool
      temp_c_at_ms: int (ticks_ms when last updated)

    Returns:
      temp_c (float|None)
    """
    if not isinstance(rtc_info, dict):
        return None

    if i2c is None:
        rtc_info["temp_ok"] = False
        return rtc_info.get("temp_c")

    now = _now_ms()

    last_ms = rtc_info.get("temp_c_at_ms", None)
    try:
        last_ms = int(last_ms) if last_ms is not None else None
    except Exception:
        last_ms = None

    # Throttle (unless forced)
    if (not force) and (last_ms is not None):
        try:
            if _ticks_diff(now, last_ms) < int(refresh_ms):
                return rtc_info.get("temp_c")
        except Exception:
            pass

    # Presence check (non-fatal)
    detected = _ds3231_detected(i2c)
    rtc_info["detected"] = bool(detected)

    if not detected:
        rtc_info["temp_ok"] = False
        rtc_info["temp_c_at_ms"] = now
        rtc_info["temp_c"] = None
        return None

    ds = None
    try:
        ds = DS3231(i2c, addr=DS3231_ADDR, probe=False)  # probe=False: avoid extra scan
        tc = _read_temp_c(ds)

        if tc is None:
            rtc_info["temp_ok"] = False
        else:
            rtc_info["temp_ok"] = True
            rtc_info["temp_c"] = float(tc)

        rtc_info["temp_c_at_ms"] = now
        return rtc_info.get("temp_c")

    except Exception:
        rtc_info["temp_ok"] = False
        rtc_info["temp_c_at_ms"] = now
        return rtc_info.get("temp_c")

    finally:
        ds = None


def sync_system_rtc_from_ds3231(i2c, min_year=2020, tz_offset_s=0):
    """
    Sync system RTC (machine.RTC) from DS3231 (assumed UTC).

    Returns dict:
      ok: bool
      synced: bool
      detected: bool
      utc: bool
      dt_utc: tuple|None -> (Y,M,D,wd0,H,M,S)
      unix: int|None
      temp_c: float|None
      reason: str|None
      error: str|None
      ticking: bool
      temp_ok: bool
      temp_c_at_ms: int|None
    """
    out = {
        "ok": False,
        "synced": False,
        "detected": False,
        "utc": True,
        "dt_utc": None,
        "unix": None,
        "temp_c": None,
        "reason": None,
        "error": None,
        "ticking": False,
        "temp_ok": False,
        "temp_c_at_ms": None,
    }

    try:
        tz_offset_s = int(tz_offset_s)
    except Exception:
        tz_offset_s = 0

    if tz_offset_s != 0:
        out["reason"] = "tz_offset_not_zero_refused"
        return out

    if i2c is None:
        out["reason"] = "no_i2c"
        return out

    # Best-effort detect
    try:
        addrs = i2c.scan() or []
        if DS3231_ADDR not in addrs:
            out["detected"] = False
            out["reason"] = "not_detected"
            return out
        out["detected"] = True
    except Exception:
        # Can't scan; we'll attempt to read anyway
        out["detected"] = True

    try:
        ds = DS3231(i2c, addr=DS3231_ADDR, probe=False)

        dt = ds.datetime()

        if not isinstance(dt, (tuple, list)) or len(dt) < 7:
            out["reason"] = "bad_datetime_tuple"
            out["ok"] = False
            out["detected"] = False
            return out

        year = _normalize_year(dt[0])
        month = int(dt[1])
        day = int(dt[2])
        wd0 = _normalize_wday(dt[3])
        hour = int(dt[4])
        minute = int(dt[5])
        sec = int(dt[6])

        out["ok"] = True
        out["detected"] = True

        # Temperature (best effort)
        tc = _read_temp_c(ds)
        if tc is not None:
            out["temp_c"] = float(tc)
            out["temp_ok"] = True
            out["temp_c_at_ms"] = _now_ms()

        # Ticking sanity (best effort, non-fatal)
        out["ticking"] = bool(ds3231_seconds_ticking(ds, sample_ms=700))

        if year is None:
            out["reason"] = "invalid_year"
            out["dt_utc"] = None
            return out

        if not (1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= sec <= 59):
            out["reason"] = "datetime_out_of_range"
            out["dt_utc"] = (year, month, day, wd0, hour, minute, sec)
            return out

        try:
            min_year = int(min_year)
        except Exception:
            min_year = 2020

        # MicroPython RP2040 time.mktime() uses epoch 2000-01-01; Unix epoch is 1970-01-01.
        # Add 946 684 800 s (30 years) so rtc["unix"] is a real Unix timestamp.
        _MP_EPOCH_OFFSET = 946_684_800

        if year < min_year:
            out["reason"] = "year_below_min"
            out["dt_utc"] = (year, month, day, wd0, hour, minute, sec)
            mp_s = _safe_mktime((year, month, day, hour, minute, sec, 0, 0))
            out["unix"] = (mp_s + _MP_EPOCH_OFFSET) if mp_s is not None else None
            out["synced"] = False
            return out

        # DS3231 is accepted as UTC: set system RTC directly
        RTC().datetime((year, month, day, wd0, hour, minute, sec, 0))
        out["dt_utc"] = (year, month, day, wd0, hour, minute, sec)
        mp_s = _safe_mktime((year, month, day, hour, minute, sec, 0, 0))
        out["unix"] = (mp_s + _MP_EPOCH_OFFSET) if mp_s is not None else None
        out["synced"] = True
        out["reason"] = None
        return out

    except Exception as e:
        out["ok"] = False
        out["synced"] = False
        out["error"] = repr(e)
        out["reason"] = "exception"
        # detected stays best-effort
        return out