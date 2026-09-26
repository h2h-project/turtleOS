# src/app/journey.py — active-journey state (turtle mode)
#
# A "journey" is an operator-marked leg of travel. The user opens the Journey
# screen (src/ui/screens/journey.py), double-clicks to flip the toggle ON, and
# from that moment every telemetry payload the scheduler builds carries this
# journey's id under flags.journey_id (see
# telemetry_scheduler._build_full_payload). Double-clicking the Journey screen
# again stamps set_arrival and clears the journey, so the tagging stops.
#
# The id is the unix second the journey started: monotonic per turtle, sortable,
# and allocatable with no server round-trip. The record persists to
# /journey_state.json so a journey survives a watchdog reboot or power cycle and
# keeps tagging until the operator explicitly "arrives".
#
# State file shape (file absent, or {}, means no journey is active):
#   {"id": 1725800000, "started_at": 1725800000, "departure": [lat, lon]}
#
# This module is a process-wide singleton: the Journey screen and the telemetry
# scheduler import the same module object, so a start()/end() on the screen side
# is visible to the scheduler's next _build_full_payload() without any plumbing.

import json

_STATE_PATH = "/journey_state.json"

_cache = {}          # last known state ({} = no active journey)
_loaded = False      # has _read() run at least once this power cycle?


_MDAYS = (0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _now_unix_seconds():
    """UTC epoch seconds from the RTC, or 0 when the clock is not yet synced.

    Mirrors TelemetryScheduler._now_unix_seconds so a journey id lines up with
    the recorded_at of the telemetry taken during it."""
    try:
        from machine import RTC
        y, mo, d, wd, hh, mm, ss, sub = RTC().datetime()
        if y < 2020:
            return 0
        leap = (y % 4 == 0) and (y % 100 != 0 or y % 400 == 0)
        leaps = (y - 1) // 4 - (y - 1) // 100 + (y - 1) // 400 - 477
        days = (y - 1970) * 365 + leaps
        for m in range(1, mo):
            days += _MDAYS[m] + (1 if m == 2 and leap else 0)
        days += d - 1
        return days * 86400 + hh * 3600 + mm * 60 + ss
    except Exception:
        try:
            return int(__import__("time").time()) + 946_684_800
        except Exception:
            return 0


def _read():
    global _cache, _loaded
    st = {}
    try:
        with open(_STATE_PATH) as f:
            raw = json.load(f)
        if isinstance(raw, dict) and raw.get("id"):
            st = raw
    except Exception:
        st = {}
    _cache = st
    _loaded = True
    return _cache


def _write(st):
    global _cache
    st = st if (isinstance(st, dict) and st.get("id")) else {}
    try:
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        import os
        os.rename(tmp, _STATE_PATH)
    except Exception:
        pass
    _cache = st


def get(force=False):
    """Return the active-journey dict, or {} when no journey is active."""
    if force or not _loaded:
        return _read()
    return _cache if isinstance(_cache, dict) else {}


def active():
    return bool(get().get("id"))


def active_id():
    return get().get("id")


def start(lat, lon):
    """Begin a journey anchored at (lat, lon). Returns the record, or None if
    the RTC is not synced (no usable id — caller should surface the failure)."""
    now_s = _now_unix_seconds()
    if now_s < 1_000_000_000:
        return None
    jid = int(now_s)
    st = {"id": jid, "started_at": jid, "departure": [lat, lon]}
    _write(st)
    return st


def end():
    """Clear the active journey. The arrival point is written to config
    (set_arrival) by the caller, not stored here."""
    _write({})
