# src/app/monotonic.py — a millisecond clock that never wraps (review B5)
#
# time.ticks_ms() wraps every 2^30 ms (12.4 days) on the ESP32 port, and
# ticks_diff() is only right for gaps under half of that (~6.2 days). Any
# "how long ago" stamp that can outlive 6.2 days (the age of the last GPS
# fix, when SAFE began, when the wind was last solved, the last good send)
# goes negative after that, so every "age < limit" test passes and stale
# data reads as fresh. A turtle at sea for months hits this for certain.
#
# ms() keeps a running total: each call adds ticks_diff(now, last call).
# That is exact as long as calls are less than ~6.2 days apart, and _bg_tick
# calls ms() on every pass (several times a second, on every screen,
# including sleep), so the gap is never more than a few seconds. The total
# is a Python int, which grows past 2^30 into a long int on the ESP32
# without wrapping: 3 months is ~7.8e9 ms, a century is ~3.2e12.
#
# It is not wall-clock time: it starts at 0 each boot and ignores RTC
# changes (phone/GPS/NTP sync), so a clock set mid-journey can't make a fix
# look older or newer than it is. Compare two ms() values with plain
# subtraction, never ticks_diff(), and never pass one to ticks_add()/
# ticks_diff() (a value above 2^31 raises OverflowError there).
#
# Called from the main loop and the telemetry background thread, so the
# read-modify-write is under a lock.

import time

try:
    import _thread
    _lock = _thread.allocate_lock()
except Exception:
    _lock = None

_st = [None, 0]   # last ticks_ms() seen, total ms since the first call


def ms():
    """Milliseconds since the first call this boot. Never wraps, never
    goes backwards."""
    if _lock is not None:
        _lock.acquire()
    try:
        now = time.ticks_ms()
        last = _st[0]
        if last is not None:
            d = time.ticks_diff(now, last)
            # d < 0 only if calls were more than half a ticks period apart
            # (nothing kept _bg_tick running). The true gap is then unknown;
            # stand still rather than step backwards.
            if d > 0:
                _st[1] += d
        _st[0] = now
        return _st[1]
    finally:
        if _lock is not None:
            _lock.release()


def age_ms(stamp):
    """ms() - stamp, or None when stamp is None."""
    if stamp is None:
        return None
    return ms() - stamp
