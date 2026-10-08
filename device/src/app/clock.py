# src/app/clock.py — where the turtle's time came from, the RTC coin-cell
# check, secure mode, and GPS time at boot.
#
# GATT contract v1, "Clock and secure mode" (docs/wrangler/README.md):
#   - the boot RTC step (main.py step_rtc) tries the DS3231, then NTP;
#   - boot() runs once at the start of run(): it settles the battery fault
#     (OSF + a flash marker), records the clock source, and arms GPS time
#     when the clock can't be trusted and secure_mode is off;
#   - the first valid RMC that GnssModule.read_nmea() sees then sets the
#     clock (every GPS reader goes through read_nmea, so one hook covers
#     the nav controller, the GPS screen, the scheduler and actions).

import os

# Shore clock_source
SRC_NONE = 0
SRC_RTC = 1
SRC_NTP = 2
SRC_GPS = 3
SRC_PHONE = 4

# 2020-01-01 .. 2099-12-31 23:59:59 UTC: the DS3231 stores 2000..2099, and
# anything before 2020 reads as "not synced" everywhere else.
TIME_MIN = 1577836800
TIME_MAX = 4102444799

# GPS dates before this firmware was written are wrong: a receiver's
# week-number rollover typically lands in 1999-2019, which a two-digit RMC
# year can't tell apart. Bump when convenient; it only needs to be in the past.
GPS_YEAR_MIN = 2026

# Kept on flash so a flat coin cell stays reported after setting the time
# clears the DS3231's OSF; cleared only by a power-on boot that proves the
# chip held its time (_settle_battery).
FAULT_FILE = "rtc_battery_fault"

_st = {"source": SRC_NONE, "battery_ok": None}
_ctx = {"i2c": None, "rtc": None, "cfg": None}    # cfg: () -> live config dict


def unix_to_dt(t):
    """Unix seconds -> (y, mo, d, wd0, hh, mm, ss), wd0 0 = Monday. Pure
    arithmetic (days-to-civil), so it doesn't depend on the port's epoch —
    time.gmtime() counts from 2000 on ESP32 but 1970 on the host."""
    days, rem = divmod(int(t), 86400)
    hh, rem = divmod(rem, 3600)
    mm, ss = divmod(rem, 60)
    wd0 = (days + 3) % 7                 # 1970-01-01 was a Thursday
    z = days + 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    mo = mp + 3 if mp < 10 else mp - 9
    y = yoe + era * 400 + (1 if mo <= 2 else 0)
    return y, mo, d, wd0, hh, mm, ss


def dt_to_unix(y, mo, d, hh, mm, ss):
    """Inverse of unix_to_dt (civil-to-days), epoch-independent too."""
    y -= 1 if mo <= 2 else 0
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (mo + (-3 if mo > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    days = era * 146097 + doe - 719468
    return days * 86400 + hh * 3600 + mm * 60 + ss


# ---------------------------------------------------------------- state

def source():
    return _st["source"]


def battery_ok():
    """True = coin cell good · False = fault (OSF seen, or not yet proven
    since) · None = not checked yet (before boot())."""
    return _st["battery_ok"]


def battery_fault():
    return _st["battery_ok"] is not True and _st["battery_ok"] is not None


def secure_requested(cfg=None):
    if not isinstance(cfg, dict):
        try:
            cfg = _ctx["cfg"]() if _ctx["cfg"] else None
        except Exception:
            cfg = None
    return bool(isinstance(cfg, dict) and cfg.get("secure_mode", False))


def secure_active(cfg=None):
    """secure_mode is on and in force: asked for, and the RTC battery is
    sound. Status bit 19."""
    return secure_requested(cfg) and _st["battery_ok"] is True


def secure_blocked(cfg=None):
    """Config asks for secure_mode but the RTC battery is faulty. Status
    bit 20. GPS time stays refused (fail closed)."""
    return secure_requested(cfg) and _st["battery_ok"] is not True


# ---------------------------------------------------------------- setting

def set_unix(t, src, i2c=None, rtc_info=None):
    """Set machine.RTC() to unix seconds `t` and write the DS3231 too
    (clearing its OSF) when it answers. Returns True if the chip took it.
    Raises ValueError outside TIME_MIN..TIME_MAX."""
    t = int(t)
    if not TIME_MIN <= t <= TIME_MAX:
        raise ValueError("time out of range")
    i2c = i2c if i2c is not None else _ctx["i2c"]
    rtc_info = rtc_info if isinstance(rtc_info, dict) else _ctx["rtc"]
    y, mo, d, wd0, hh, mm, ss = unix_to_dt(t)
    from machine import RTC
    RTC().datetime((y, mo, d, wd0, hh, mm, ss, 0))
    chip = False
    if i2c is not None:
        try:
            from src.drivers.ds3231 import DS3231
            ds = DS3231(i2c, probe=False)
            ds.datetime((y, mo, d, wd0 + 1, hh, mm, ss))   # DS3231: 1 = Monday
            ds.clear_lost_power()
            chip = True
        except Exception as e:
            print("[TIME] DS3231 write failed:", repr(e))
    if isinstance(rtc_info, dict):
        rtc_info["synced"] = True
        rtc_info["unix"] = t
        rtc_info["reason"] = None
    _st["source"] = src
    disarm_gps()
    print("[TIME] set to {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d} UTC from {}{}".format(
        y, mo, d, hh, mm, ss, _SRC_NAMES[src],
        " (+DS3231)" if chip else " (system clock only)"))
    return chip


_SRC_NAMES = ("nothing", "RTC chip", "NTP", "GPS", "phone")


# ---------------------------------------------------------------- GPS time

def arm_gps():
    """Let the next valid RMC set the clock, unless secure_mode is asked
    for (even blocked) or the clock is already trustworthy."""
    if secure_requested():
        disarm_gps()
        return False
    if _st["source"] in (SRC_NTP, SRC_GPS, SRC_PHONE):
        return False
    if _st["source"] == SRC_RTC and _st["battery_ok"] is True:
        return False
    try:
        from src.sensors import xiao_gnss
        xiao_gnss.rmc_hook = _on_rmc
        print("[TIME] waiting for a GPS fix to set the clock")
        return True
    except Exception as e:
        print("[TIME] GPS time hook failed:", repr(e))
        return False


def disarm_gps():
    try:
        from src.sensors import xiao_gnss
        xiao_gnss.rmc_hook = None
    except Exception:
        pass


def _checksum_ok(line):
    try:
        star = line.rfind("*")
        if star < 1:
            return False
        x = 0
        for ch in line[1:star]:
            x ^= ord(ch)
        return x == int(line[star + 1:star + 3], 16)
    except Exception:
        return False


def rmc_unix(line):
    """Unix seconds from a valid $G?RMC (status A, good checksum, year
    GPS_YEAR_MIN or later), else None."""
    try:
        if not _checksum_ok(line):
            return None
        p = line.split(",")
        if len(p) < 10 or p[2] != "A":
            return None
        hms, dmy = p[1], p[9]
        if len(hms) < 6 or len(dmy) != 6:
            return None
        hh, mm, ss = int(hms[0:2]), int(hms[2:4]), int(hms[4:6])
        d, mo, y = int(dmy[0:2]), int(dmy[2:4]), 2000 + int(dmy[4:6])
        if y < GPS_YEAR_MIN:
            return None
        if not (1 <= mo <= 12 and 1 <= d <= 31 and hh < 24 and mm < 60 and ss < 60):
            return None
        t = dt_to_unix(y, mo, d, hh, mm, ss)
        return t if TIME_MIN <= t <= TIME_MAX else None
    except Exception:
        return None


def _on_rmc(line):
    """GnssModule.read_nmea() hook while armed. Re-checks secure_mode in
    case it was turned on since arming."""
    if secure_requested():
        disarm_gps()
        return
    t = rmc_unix(line)
    if t is None:
        return
    try:
        set_unix(t, SRC_GPS)
    except Exception as e:
        disarm_gps()
        print("[TIME] GPS time set failed:", repr(e))


# ---------------------------------------------------------------- boot

def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _power_on_boot():
    try:
        import machine
        return machine.reset_cause() == machine.PWRON_RESET
    except Exception:
        return False


def _settle_battery(rtc_info):
    """OSF (read by the boot RTC step into rtc_info["battery_ok"]) plus the
    flash marker -> True (sound) / False (fault)."""
    if not rtc_info.get("detected"):
        return False
    osf = rtc_info.get("battery_ok") is False
    marked = _exists(FAULT_FILE)
    if osf:
        if not marked:
            try:
                with open(FAULT_FILE, "w") as f:
                    f.write("OSF")
            except Exception:
                pass
        return False
    if not marked:
        return True
    # Marked earlier. Proven fixed only when the chip kept a valid time
    # through a real power-off.
    if _power_on_boot() and rtc_info.get("synced") and rtc_info.get("source") != "ntp":
        try:
            os.remove(FAULT_FILE)
        except Exception:
            pass
        print("[RTC] coin cell held the time through a power-off: battery fault cleared")
        return True
    return False


def boot(rtc_info, i2c, cfg_get):
    """Once, at the start of run(). cfg_get() returns the live config
    (run() swaps the dict each loop). Returns battery_ok."""
    rtc_info = rtc_info if isinstance(rtc_info, dict) else {}
    _ctx["i2c"] = i2c
    _ctx["rtc"] = rtc_info
    _ctx["cfg"] = cfg_get
    if rtc_info.get("synced"):
        _st["source"] = SRC_NTP if rtc_info.get("source") == "ntp" else SRC_RTC
    ok = _settle_battery(rtc_info)
    _st["battery_ok"] = ok
    if not ok:
        print("[RTC] battery fault: replace the DS3231 coin cell. The clock is lost "
              "whenever the turtle loses power.")
    if secure_requested():
        if ok:
            print("[SECURE] secure mode on: GPS time is not trusted")
        else:
            print("[SECURE] secure mode BLOCKED by the RTC battery fault "
                  "(GPS time still refused)")
    arm_gps()
    return ok


def set_secure_mode(on):
    """SECURE_MODE_SET. Returns (ok, reason): reason 1 = RTC battery fault
    when turning on was refused."""
    if on and _st["battery_ok"] is not True:
        return False, 1
    from src.app.actions import write_config
    if write_config({"secure_mode": bool(on)}) is None:     # mirrors the live dict too
        raise OSError("config write failed")
    if on:
        disarm_gps()
    else:
        arm_gps()
    print("[SECURE] secure mode", "on" if on else "off")
    return True, 0
