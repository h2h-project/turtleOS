#!/usr/bin/env python3
# tests/clock_host.py — check src/app/clock.py on the host
#
#   python3 tests/clock_host.py        (from the repo root; ~2 s)
#
# Runs the real clock.py with machine / I2C faked, in a scratch directory
# (it writes the rtc_battery_fault marker there):
#   - unix_to_dt / dt_to_unix against datetime, both ways;
#   - RMC time: checksum, status A, range;
#   - the RTC battery fault: OSF writes the marker, a soft reboot keeps it,
#     a power-on boot with a held time clears it;
#   - GPS time is armed only when the clock can't be trusted and
#     secure_mode is off (even when secure mode is blocked);
#   - secure mode refuses to turn on with a battery fault.
# Exit code 0 = all good.

import datetime
import os
import random
import shutil
import sys
import tempfile
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVICE = os.path.join(REPO, "device")

work = tempfile.mkdtemp(prefix="clock_host_")
shutil.copytree(os.path.join(DEVICE, "src"), os.path.join(work, "src"))
shutil.copy(os.path.join(DEVICE, "config.py"), work)
os.chdir(work)
sys.path.insert(0, work)

rtc_now = [None]
mach = types.ModuleType("machine")
mach.PWRON_RESET, mach.SOFT_RESET = 1, 5
mach.cause = mach.PWRON_RESET
mach.reset_cause = lambda: mach.cause


class _RTC:
    def datetime(self, t=None):
        if t is None:
            return rtc_now[0]
        rtc_now[0] = tuple(t)


mach.RTC = _RTC
mach.UART = mach.Pin = mach.I2C = mach.SoftI2C = object
sys.modules["machine"] = mach

from src.app import clock          # noqa: E402
from src.sensors import xiao_gnss  # noqa: E402

fails = []


def check(cond, what):
    if not cond:
        fails.append(what)


# ---- date maths
EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
rnd = random.Random(1)
for _ in range(100_000):
    t = rnd.randint(0, 4_102_444_799)
    d = EPOCH + datetime.timedelta(seconds=t)
    y, mo, dd, wd0, hh, mm, ss = clock.unix_to_dt(t)
    if (y, mo, dd, wd0, hh, mm, ss) != (d.year, d.month, d.day, d.weekday(), d.hour, d.minute, d.second):
        fails.append("unix_to_dt %d" % t)
        break
    if clock.dt_to_unix(y, mo, dd, hh, mm, ss) != t:
        fails.append("dt_to_unix %d" % t)
        break


# ---- RMC
def nmea(body):
    x = 0
    for ch in body:
        x ^= ord(ch)
    return "$%s*%02X" % (body, x)


good = nmea("GNRMC,083015.000,A,3135.0000,N,03416.2000,E,0.10,45.0,081026,,,A")
check(clock.rmc_unix(good) == int(datetime.datetime(2026, 10, 8, 8, 30, 15,
                                                    tzinfo=datetime.timezone.utc).timestamp()), "rmc good")
check(clock.rmc_unix(good[:-2] + "00") is None, "rmc bad checksum")
check(clock.rmc_unix(nmea("GNRMC,083015.000,V,,,,,,,081026,,,N")) is None, "rmc status V")
check(clock.rmc_unix(nmea("GNRMC,000012.000,A,3135.0000,N,03416.2000,E,0,0,060419,,,A")) is None,
      "rmc 2019 (GPS week rollover junk) refused")


# ---- boot helpers
class FakeDS:
    def __init__(self, *a, **k):
        pass

    def datetime(self, t=None):
        FakeDS.written = t

    def clear_lost_power(self):
        FakeDS.osf_cleared = True


sys.modules["src.drivers.ds3231"] = types.SimpleNamespace(DS3231=FakeDS)
cfg = {"secure_mode": False}


def boot(osf, synced, cause, detected=True, src=None):
    clock._st["source"] = clock.SRC_NONE
    xiao_gnss.rmc_hook = None
    mach.cause = cause
    info = {"detected": detected, "battery_ok": not osf, "synced": synced}
    if src:
        info["source"] = src
    clock.boot(info, object(), lambda: cfg)
    return info


# Flat coin cell: OSF at boot, clock 2000 -> fault, marker, GPS armed
boot(osf=True, synced=False, cause=mach.PWRON_RESET)
check(clock.battery_fault() and os.path.exists(clock.FAULT_FILE), "OSF -> fault + marker")
check(xiao_gnss.rmc_hook is not None, "unsynced, not secure -> GPS armed")

# GPS sets the clock once, through read_nmea's hook
xiao_gnss.rmc_hook(good)
check(clock.source() == clock.SRC_GPS and rtc_now[0][:3] == (2026, 10, 8), "GPS set the clock")
check(xiao_gnss.rmc_hook is None, "GPS hook disarmed after one set")
check(getattr(FakeDS, "osf_cleared", False), "GPS time written to the DS3231")

# Soft reboot: OSF now clear (we cleared it), but the marker keeps the fault
boot(osf=False, synced=True, cause=mach.SOFT_RESET)
check(clock.battery_fault(), "soft reboot keeps the fault")
check(xiao_gnss.rmc_hook is not None, "RTC time with a battery fault -> GPS still armed")

# Secure mode can't turn on with the fault
check(clock.set_secure_mode(True) == (False, 1), "secure mode refused with fault")

# Secure mode in config but blocked: GPS still refused
cfg["secure_mode"] = True
boot(osf=False, synced=True, cause=mach.SOFT_RESET)
check(clock.secure_blocked() and not clock.secure_active(), "secure mode blocked")
check(xiao_gnss.rmc_hook is None, "blocked secure mode still refuses GPS time")
cfg["secure_mode"] = False

# Power-on with OSF clear and a held time proves the new coin cell
boot(osf=False, synced=True, cause=mach.PWRON_RESET)
check(clock.battery_ok() is True and not os.path.exists(clock.FAULT_FILE), "power-on proves battery")
check(xiao_gnss.rmc_hook is None, "good RTC -> GPS not armed")

# ...but not when the time came from NTP rather than the chip
open(clock.FAULT_FILE, "w").close()
boot(osf=False, synced=True, cause=mach.PWRON_RESET, src="ntp")
check(clock.battery_fault(), "NTP time doesn't prove the battery")
os.remove(clock.FAULT_FILE)

# No RTC chip at all counts as a fault
boot(osf=False, synced=False, cause=mach.PWRON_RESET, detected=False)
check(clock.battery_fault(), "no DS3231 -> fault")

# Healthy turtle: secure mode on, GPS hook re-check
boot(osf=False, synced=True, cause=mach.PWRON_RESET)
cfg["secure_mode"] = True
check(clock.secure_active(), "secure mode active with a good battery")
xiao_gnss.rmc_hook = clock._on_rmc            # armed before it was turned on
before = rtc_now[0]
clock._on_rmc(nmea("GNRMC,120000.000,A,3135.0000,N,03416.2000,E,0,0,010130,,,A"))
check(rtc_now[0] == before and xiao_gnss.rmc_hook is None, "secure mode ignores a GPS time already armed")

shutil.rmtree(work, ignore_errors=True)
if fails:
    print("FAIL:", *fails, sep="\n  ")
    sys.exit(1)
print("clock: all checks passed")
