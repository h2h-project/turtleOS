# src/app/journey.py — journeys: the open one, the ones that ended, and the
# start/end events telemetry still has to report (turtle mode)
#
# A "journey" is an operator-marked leg of travel. The user opens the Journey
# screen (src/ui/screens/journey.py) or Wrangler sends JOURNEY_START, and
# from that moment every telemetry payload the scheduler builds carries this
# journey's id under flags.journey_id (see
# telemetry_scheduler._build_full_payload). Ending it (the screen again, or
# JOURNEY_END) stamps set_arrival and stops the tagging.
#
# The id is the unix second the journey started: monotonic per turtle, sortable,
# and allocatable with no server round-trip.
#
# Field names follow the turtle data dictionary (docs/wrangler/README.md,
# "Turtle data dictionary"). One journey record:
#   {"id", "started_at", "departure": [lat, lon],
#    "destination": [lat, lon] | None, "destination_source": 0-4,
#    "ended_at": unix s | None, "arrival": [lat, lon] | None}
# "destination" is the active target when the record was last written (at
# the start, then at the end); "destination_source" is the Targets
# active_source code (src/nav/waypoints.py SRC_*). "ended_at" is None only
# if the clock was lost in between; "arrival" is None without a GPS fix.
#
# /journey_state.json (device-owned; the sync/install scripts exclude it):
#   the open journey's fields at the top level (absent = none open), plus
#   "history": ended journeys, newest first, at most HISTORY_MAX, and
#   "events":  start/end events not yet put into a telemetry reading, oldest
#              first, at most EVENTS_MAX. The scheduler takes them with
#              pending_event() / event_taken() (v2 plan, Phase B).
# A journey survives a watchdog reboot or power cycle and keeps tagging
# until the operator ends it. Old firmware reading this file still finds
# "id" at the top level and ignores the rest.
#
# This module is a process-wide singleton: the Journey screen, the Bluetooth
# commands and the telemetry scheduler import the same module object.

import json

_STATE_PATH = "/journey_state.json"

HISTORY_MAX = 20     # ended journeys kept (each well under 200 bytes)
EVENTS_MAX = 10      # unreported events kept; the oldest go first

_cache = {}          # the open journey ({} = none)
_history = []        # ended journeys, newest first
_events = []         # unreported start/end events, oldest first
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
    global _cache, _history, _events, _loaded
    raw = {}
    try:
        with open(_STATE_PATH) as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raw = {}
    except Exception:
        raw = {}
    hist = raw.get("history")
    evs = raw.get("events")
    _history = [h for h in hist if isinstance(h, dict)] if isinstance(hist, list) else []
    _events = [e for e in evs if isinstance(e, dict)] if isinstance(evs, list) else []
    _cache = {}
    if raw.get("id"):
        for k in raw:
            if k not in ("history", "events"):
                _cache[k] = raw[k]
    _loaded = True
    return _cache


def _save():
    """Write the open journey, history and events (tmp + rename)."""
    st = dict(_cache) if _cache.get("id") else {}
    if _history:
        st["history"] = _history[:HISTORY_MAX]
    if _events:
        st["events"] = _events[-EVENTS_MAX:]
    try:
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        import os
        os.rename(tmp, _STATE_PATH)
    except Exception:
        pass


def _ensure():
    if not _loaded:
        _read()


def _active_target():
    """(destination [lat, lon] or None, destination_source) — the final point
    of whatever the nav loop steers for, resolved the way WaypointSequencer
    does it."""
    try:
        from config import load_config
        from src.nav.waypoints import WaypointSequencer
        wps, src = WaypointSequencer._resolve(load_config() or {})
        if wps:
            last = wps[-1]
            return [last[0], last[1]], src
        return None, src
    except Exception:
        return None, 0


def get(force=False):
    """Return the open journey's dict, or {} when no journey is open."""
    if force or not _loaded:
        _read()
    return _cache if isinstance(_cache, dict) else {}


def active():
    return bool(get().get("id"))


def active_id():
    return get().get("id")


def history():
    """Ended journeys, newest first (copies)."""
    _ensure()
    return [dict(h) for h in _history]


def last():
    """The most recently ended journey, or None."""
    _ensure()
    return dict(_history[0]) if _history else None


def start(lat, lon):
    """Begin a journey anchored at (lat, lon). Returns the record, or None if
    the RTC is not synced (no usable id — caller should surface the failure)."""
    _ensure()
    now_s = _now_unix_seconds()
    if now_s < 1_000_000_000:
        return None
    global _cache
    jid = int(now_s)
    dest, src = _active_target()
    _cache = {"id": jid, "started_at": jid, "departure": [lat, lon],
              "destination": dest, "destination_source": src}
    _events.append({"event": "start", "journey": dict(_cache)})
    _save()
    return dict(_cache)


def end(arrival=None):
    """Close the open journey and keep it in the history. [arrival] is the
    [lat, lon] the caller stamped (also written to config set_arrival), or
    None without a fix. Returns the ended record, or None if none was open."""
    _ensure()
    global _cache, _history
    if not _cache.get("id"):
        return None
    rec = dict(_cache)
    now_s = _now_unix_seconds()
    rec["ended_at"] = int(now_s) if now_s >= 1_000_000_000 else None
    rec["arrival"] = [arrival[0], arrival[1]] if arrival else None
    dest, src = _active_target()
    if dest is not None:
        rec["destination"], rec["destination_source"] = dest, src
    full = [rec] + _history
    _history = full[:HISTORY_MAX]
    for gone in full[HISTORY_MAX:]:
        _drop_track(gone.get("id") or 0)
    _cache = {}
    _events.append({"event": "end", "journey": dict(rec)})
    _save()
    return dict(rec)


def pending_event():
    """The oldest start/end event no telemetry reading has carried yet, as
    {"event": "start" | "end", "journey": record}, or None."""
    _ensure()
    return dict(_events[0]) if _events else None


def event_taken():
    """The scheduler put pending_event() into a reading: drop it."""
    _ensure()
    if _events:
        del _events[0]
        _save()


# ---------------------------------------------------------------- track
#
# Each journey's track lives in its own file, /journey_<id>.trk: packed
# TRACK_POINT records, oldest first, one appended by the telemetry scheduler
# for every reading it builds with a fix while the journey is open (sent or
# queued alike). GATT JOURNEY_TRACK pages straight out of it with a seek, so
# a page costs one small read. (2.6.0 scanned telemetry_queue.json instead:
# up to 5000 JSON lines per request, run inside the button loop, which froze
# the screens for seconds while Wrangler synced.)
#
# A file holds at most TRACK_MAX points; past that every second point is
# dropped, so a long journey keeps its whole shape at a coarser spacing.
# Files of journeys that fall off the history are removed (end()).

_NAV_STATES = {"BOOT": 0, "ACQUIRE": 1, "SAIL_NAV": 2, "ARRIVAL": 3, "SAFE": 4}
TRACK_POINT = "<IiiB"                    # recorded_at, lat_e7, lon_e7, machine_state
TRACK_POINT_SIZE = 13
TRACK_MAX = 1000                         # points per journey file (13 kB)
_TRACK_DIR = "/"


def _e7(deg):
    return int(round(float(deg) * 1e7))


def _track_path(jid):
    return "{}journey_{}.trk".format(_TRACK_DIR, int(jid))


def _size(path):
    try:
        import os
        return os.stat(path)[6]
    except OSError:
        return 0


def record_point(jid, recorded_at, lat, lon, machine_state=None):
    """Append one reading to journey [jid]'s track file. Never raises."""
    try:
        import struct
        rec = struct.pack(TRACK_POINT, int(recorded_at) & 0xFFFFFFFF, _e7(lat), _e7(lon),
                          _NAV_STATES.get(machine_state, 0xFF))
        path = _track_path(jid)
        with open(path, "ab") as f:
            f.write(rec)
        if _size(path) >= TRACK_MAX * TRACK_POINT_SIZE:
            _thin(path)
    except Exception:
        pass


def _thin(path):
    """Keep every second point (the newest always), a few at a time: RAM is short."""
    import os
    size = TRACK_POINT_SIZE
    n = _size(path) // size
    tmp = path + ".tmp"
    with open(path, "rb") as src, open(tmp, "wb") as dst:
        for i in range(n):
            rec = src.read(size)
            if len(rec) < size:
                break
            if i % 2 == (n - 1) % 2:
                dst.write(rec)
    os.rename(tmp, path)


def track_count(jid):
    """How many points journey [jid]'s track holds (0 = no file)."""
    return _size(_track_path(jid)) // TRACK_POINT_SIZE


def track_page(jid, start, n):
    """Up to [n] packed points of journey [jid]'s track from index [start]."""
    if n <= 0:
        return b""
    try:
        with open(_track_path(jid), "rb") as f:
            f.seek(start * TRACK_POINT_SIZE)
            data = f.read(n * TRACK_POINT_SIZE)
        return data[:len(data) - len(data) % TRACK_POINT_SIZE]
    except OSError:
        return b""


def _drop_track(jid):
    try:
        import os
        os.remove(_track_path(jid))
    except Exception:
        pass
