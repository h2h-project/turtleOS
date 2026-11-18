# src/app/watchdog.py — hardware watchdog + crash recovery
#
# The ESP32-S3's task watchdog (machine.WDT) is a countdown in the chip. The
# firmware feeds it from the places a healthy turtle passes through all the
# time (run()'s _bg_tick and every button poll). If the main loop hangs, the
# feeds stop and the chip resets itself. A crash (an exception out of run())
# doesn't wait for the countdown: handle_crash() logs it and resets.
#
# Two properties of the ESP32 watchdog shape this module:
#   - It can't be stopped once started, and it keeps running at the REPL and
#     across a soft reset. relax() therefore stretches the timeout to 24 h
#     for bench work (Ctrl-C, the Synker, debug mode).
#   - Creating machine.WDT again changes the timeout of the running one.
#     tests/wdt_spike.py checks both on the board.
#
# Never feed from the background thread: a hung main loop must starve it.
#
# Config: watchdog_enabled (default true), watchdog_timeout_s (default 60).
# Files (device-owned): /crash.txt, /crash_count, /reset_log.txt.
#
# machine is imported inside functions so host tests can import this module.

import time

BOOT_TIMEOUT_S = 120          # boot pipeline: fed once per boot step
DEFAULT_TIMEOUT_S = 60
MIN_TIMEOUT_S = 20
MAX_TIMEOUT_S = 600
RELAX_S = 86400               # "off" for bench work: 24 h

CRASH_FILE = "crash.txt"
CRASH_COUNT_FILE = "crash_count"
RESET_LOG_FILE = "reset_log.txt"
CRASH_FILE_MAX = 8000         # bytes; the newest half is kept past this
RESET_LOG_MAX = 4000

# Crash loop: the first few crashes in a row reset quickly; after that the
# turtle waits before each reset. It never halts. The count clears once the
# app has run for HEALTHY_AFTER_MS.
QUICK_CRASHES = 3
QUICK_RESET_S = 3
SLOW_RESET_S = 600
HEALTHY_AFTER_MS = 300000

_wdt = None
_relaxed = False
_enabled = None               # config watchdog_enabled, read once
_healthy_done = False
try:
    _start_ms = time.ticks_ms()
except Exception:
    _start_ms = 0


# ---------------------------------------------------------------- config

def enabled():
    """config.json watchdog_enabled (default True). Read straight from the
    file, once: this runs before the boot pipeline has loaded the config."""
    global _enabled
    if _enabled is None:
        _enabled = True
        try:
            import json
            with open("config.json") as f:
                v = json.load(f).get("watchdog_enabled", True)
            if isinstance(v, str):
                v = v.strip().lower() not in ("0", "false", "no", "off", "")
            _enabled = bool(v)
        except Exception:
            pass
    return _enabled


def timeout_s(cfg):
    """watchdog_timeout_s from cfg, clamped."""
    try:
        t = int((cfg or {}).get("watchdog_timeout_s", DEFAULT_TIMEOUT_S))
    except Exception:
        t = DEFAULT_TIMEOUT_S
    return max(MIN_TIMEOUT_S, min(MAX_TIMEOUT_S, t))


# ---------------------------------------------------------------- watchdog

def _make(seconds):
    from machine import WDT
    return WDT(timeout=int(seconds) * 1000)


def start(seconds=DEFAULT_TIMEOUT_S):
    """Start the watchdog, or change the timeout of the running one.
    Returns True when it is running at that timeout."""
    global _wdt, _relaxed
    if not enabled():
        return False
    try:
        _wdt = _make(seconds)
        _relaxed = False
        print("[WDT] watchdog on, {} s".format(int(seconds)))
        return True
    except Exception as e:
        print("[WDT] start failed:", repr(e))
        return False


def feed():
    w = _wdt
    if w is not None:
        w.feed()


def relax():
    """Stretch the timeout to 24 h so the REPL and uploads aren't reset.
    Also catches a watchdog left running by the app before a soft reset,
    which this module has no handle to."""
    global _wdt, _relaxed
    try:
        _wdt = _make(RELAX_S)
        _relaxed = True
        print("[WDT] relaxed to 24 h")
        return True
    except Exception as e:
        print("[WDT] relax failed:", repr(e))
        return False


def active():
    """True while the watchdog is running at a working (not relaxed) timeout."""
    return _wdt is not None and not _relaxed


def _soft_reset():
    try:
        import machine
        return machine.reset_cause() == machine.SOFT_RESET
    except Exception:
        return False


def boot(debug=False):
    """First thing in device/main.py. Normal boot: start the watchdog for the
    boot pipeline. Debug mode or watchdog_enabled false: don't start one, and
    relax any that survived a soft reset."""
    if debug or not enabled():
        if _soft_reset():
            relax()
        return False
    return start(BOOT_TIMEOUT_S)


# ---------------------------------------------------------------- records

def _stamp():
    try:
        from machine import RTC
        y, mo, d, _wd, hh, mm, ss, _sub = RTC().datetime()
        return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(y, mo, d, hh, mm, ss)
    except Exception:
        return "?"


def _size(path):
    try:
        import os
        return os.stat(path)[6]
    except Exception:
        return 0


def _trim(path, limit):
    """Past limit bytes, keep the newest half (whole lines)."""
    if _size(path) <= limit:
        return
    try:
        with open(path) as f:
            f.seek(_size(path) - limit // 2)
            f.readline()                      # drop the partial line
            tail = f.read()
        with open(path, "w") as f:
            f.write(tail)
    except Exception:
        pass


def _reset_cause_name():
    try:
        import machine
        c = machine.reset_cause()
        for name in ("PWRON_RESET", "HARD_RESET", "WDT_RESET", "DEEPSLEEP_RESET", "SOFT_RESET"):
            if getattr(machine, name, None) == c:
                return name
        return str(c)
    except Exception:
        return "?"


def note_boot():
    """Print why the chip last reset and append it to /reset_log.txt. Call
    once the RTC is synced so the line carries a real time."""
    cause = _reset_cause_name()
    crashes = crash_count()
    print("[BOOT] reset cause: {}  crashes in a row: {}".format(cause, crashes))
    try:
        _trim(RESET_LOG_FILE, RESET_LOG_MAX)
        with open(RESET_LOG_FILE, "a") as f:
            f.write("{} {} crashes={}\n".format(_stamp(), cause, crashes))
    except Exception:
        pass
    return cause


def crash_count():
    try:
        with open(CRASH_COUNT_FILE) as f:
            return int(f.read().strip() or 0)
    except Exception:
        return 0


def _set_crash_count(n):
    try:
        with open(CRASH_COUNT_FILE, "w") as f:
            f.write(str(int(n)))
    except Exception:
        pass


def record_crash(e):
    """Append the traceback to /crash.txt and bump the crash count.
    Returns the new count."""
    try:
        _trim(CRASH_FILE, CRASH_FILE_MAX)
        with open(CRASH_FILE, "a") as f:
            f.write("--- {} ---\n".format(_stamp()))
            try:
                import sys
                sys.print_exception(e, f)
            except Exception:
                f.write(repr(e) + "\n")
    except Exception:
        pass
    n = crash_count() + 1
    _set_crash_count(n)
    return n


def reset_delay_s(count):
    """Seconds to wait before the reset that follows crash number count."""
    return QUICK_RESET_S if count <= QUICK_CRASHES else SLOW_RESET_S


def handle_crash(e):
    """run() raised: log it, wait, reset. Does not return. Ctrl-C during the
    wait relaxes the watchdog and hands over the REPL."""
    n = record_crash(e)
    wait = reset_delay_s(n)
    print("[WDT] crash #{} in a row, resetting in {} s".format(n, wait))
    try:
        for _ in range(wait):
            feed()
            time.sleep_ms(1000)
    except KeyboardInterrupt:
        relax()
        raise
    import machine
    machine.reset()


def healthy(now_ms=None):
    """Called from _bg_tick. Once the app has run for HEALTHY_AFTER_MS the
    crash count is cleared, so only crashes in quick succession add up."""
    global _healthy_done
    if _healthy_done:
        return
    now = time.ticks_ms() if now_ms is None else now_ms
    if time.ticks_diff(now, _start_ms) < HEALTHY_AFTER_MS:
        return
    _healthy_done = True
    if _size(CRASH_COUNT_FILE):
        try:
            import os
            os.remove(CRASH_COUNT_FILE)
            print("[WDT] healthy for 5 min, crash count cleared")
        except Exception:
            pass
