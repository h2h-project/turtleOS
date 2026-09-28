# src/app/actions.py — the side-effecting half of every OLED gesture
#
# Each function here performs one operator action and returns
#     (code, info)
# where `code` is a GATT contract v1 result code (docs/wrangler/README.md →
# Result codes) and `info` is a small dict of details for whoever reports the
# outcome. The OLED screens call these and draw the outcome; the Bluetooth
# command handler (src/net/ble_service.py, Phase 4+) calls the same functions
# and notifies the outcome. One code path means a gesture and its Bluetooth
# command can never drift apart — the server mirroring (PATCH /v1/device),
# the nav state changes and the config writes all come along automatically.
#
# Rules for anything added here:
#   - No drawing, no button handling, no sleeps for effect. Screens own that.
#   - Config writes go through config.update_config(), never save_config() on
#     a dict loaded earlier.
#   - Lazy imports inside functions (CLAUDE.md gotcha 3).
#   - Never raise: an unexpected exception becomes ERR_INTERNAL and is printed.

import time

# ---- GATT contract v1 result codes ----
OK = 0x00
IN_PROGRESS = 0x01
ERR_UNKNOWN_OPCODE = 0x10
ERR_BAD_LENGTH = 0x11
ERR_BAD_VALUE = 0x12
ERR_BUSY = 0x13
ERR_NOT_BONDED = 0x14
ERR_WRONG_STATE = 0x15
ERR_NO_GPS_FIX = 0x20
ERR_RTC_NOT_SYNCED = 0x21
ERR_NO_HARDWARE = 0x22
ERR_NO_TARGET = 0x23
ERR_WIFI_FAILED = 0x30
ERR_API_FAILED = 0x31
ERR_NOT_STAMPED = 0x32
ERR_INTERNAL = 0x7F

# A cached fix older than this is not "here".
FIX_MAX_AGE_MS = 5000
# How long to listen on the GPS UART for a fresh RMC when the cache is stale.
FIX_LISTEN_MS = 2200


# ---------------------------------------------------------------- config

# run() re-reads config.json only at the top of each main-loop iteration and
# hands that dict (_cfg_cell[0]) to the background tick and every carousel.
# While a screen — or a Bluetooth command — is running, the loop isn't
# re-reading, so a write that only reached the file would not reach the
# background tick until the carousel exits. run() binds its cell here once;
# write_config() mirrors every change into the live dict as well.
_cfg_cell = None


def bind_cfg_cell(cell):
    global _cfg_cell
    _cfg_cell = cell


def write_config(changes):
    """update_config() plus the live-dict mirror. Returns the saved config,
    or None if the write failed."""
    try:
        from config import update_config
        cfg = update_config(changes)
    except Exception as e:
        print("[ACTION] config write failed:", repr(e))
        return None
    try:
        live = _cfg_cell[0] if _cfg_cell else None
        if isinstance(live, dict):
            for k in changes:
                live[k] = cfg.get(k)
            # Derived mirror rebuilt by _normalize_types()
            live["telemetry_enabled"] = cfg.get("telemetry_enabled")
    except Exception:
        pass
    return cfg


# ---------------------------------------------------------------- helpers

def current_fix(gps=None, max_age_ms=FIX_MAX_AGE_MS, listen_ms=FIX_LISTEN_MS):
    """Return the turtle's own position as (lat, lon), or None.

    Uses the shared fix cache (src/nav/gpsfix.py) when it is fresh; otherwise,
    if a GPS handle is given, listens on the UART for up to `listen_ms` and
    publishes whatever RMC it reads back to the cache (CLAUDE.md: whoever
    drains the GPS UART must publish to gpsfix).
    """
    try:
        from src.nav import gpsfix
    except Exception:
        return None

    try:
        la, lo, age = gpsfix.get()
        if la is not None and age is not None and age < max_age_ms:
            return (la, lo)
    except Exception:
        pass

    if gps is None:
        return None

    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < listen_ms:
        try:
            line = gps.read_nmea(max_ms=40)
        except Exception:
            line = None
        if line and "RMC" in line:
            la, lo, cog = gpsfix.parse_rmc(line)
            if la is not None:
                try:
                    gpsfix.update(la, lo, cog)
                except Exception:
                    pass
                return (la, lo)
        time.sleep_ms(10)
    return None


def save_set_fields(updates):
    """Write operator set_* fields to config, then best-effort mirror them to
    hopeturtles.org (PATCH /v1/device) for the dashboard. config.json stays
    authoritative; a failed PATCH is re-pushed on the next online boot."""
    cfg = write_config(updates)
    if cfg is None:
        return
    try:
        from src.net.device_client import patch_set_fields
        patch_set_fields(cfg, {k: v for k, v in updates.items() if k.startswith("set_")})
    except Exception:
        pass


# ---------------------------------------------------------------- journey

def _enter_sail_nav():
    """Opening a journey puts the turtle into SAIL-NAV. No-op if the state
    machine refuses (e.g. the turtle is in SAFE after a fault)."""
    try:
        from src.nav import state_machine as sm
        if sm.get_state() == sm.BOOT:
            # Boot normally advances BOOT->ACQUIRE; cover the race where a
            # journey is started first.
            sm.set_state(sm.ACQUIRE, "journey started (from boot)")
        sm.set_state(sm.SAIL_NAV, "journey started")
    except Exception as e:
        print("[JOURNEY] sail-nav enter failed:", repr(e))


def _stand_down():
    """Ending a journey drops SAIL-NAV back to ACQUIRE. Left alone if the
    turtle has meanwhile gone to SAFE or already reached ARRIVAL."""
    try:
        from src.nav import state_machine as sm
        if sm.get_state() == sm.SAIL_NAV:
            sm.set_state(sm.ACQUIRE, "journey ended")
    except Exception as e:
        print("[JOURNEY] stand-down failed:", repr(e))


def journey_active():
    try:
        from src.app import journey
        return bool(journey.active())
    except Exception:
        return False


def journey_start(gps=None):
    """Open a journey at the turtle's current position.

    OK                  info: {"journey_id", "lat", "lon"}
    ERR_WRONG_STATE     a journey is already open
    ERR_NO_GPS_FIX      no fix — nothing to anchor the departure to
    ERR_RTC_NOT_SYNCED  no synced clock — no usable journey id
    """
    try:
        from src.app import journey
        if journey.active():
            return ERR_WRONG_STATE, {"journey_id": journey.active_id()}

        loc = current_fix(gps)
        if loc is None:
            print("[JOURNEY] start: no GPS fix")
            return ERR_NO_GPS_FIX, {}

        rec = journey.start(loc[0], loc[1])
        if rec is None:
            print("[JOURNEY] start: RTC not synced")
            return ERR_RTC_NOT_SYNCED, {}

        save_set_fields({"set_departure": [loc[0], loc[1]]})
        _enter_sail_nav()
        print("[JOURNEY] started id={} dep={:.6f},{:.6f}".format(rec.get("id"), loc[0], loc[1]))
        return OK, {"journey_id": rec.get("id"), "lat": loc[0], "lon": loc[1]}
    except Exception as e:
        print("[JOURNEY] start err:", repr(e))
        return ERR_INTERNAL, {}


def journey_end(gps=None):
    """Close the open journey. A lost fix still ends it — the operator is
    never trapped in a journey — but set_arrival is then left unchanged.

    OK                  info: {"journey_id", "arrival_stamped", "lat", "lon"}
    ERR_WRONG_STATE     no journey is open
    """
    try:
        from src.app import journey
        if not journey.active():
            return ERR_WRONG_STATE, {}

        jid = journey.active_id()
        loc = current_fix(gps)
        if loc is not None:
            save_set_fields({"set_arrival": [loc[0], loc[1]]})
        journey.end()
        _stand_down()

        if loc is not None:
            print("[JOURNEY] arrived id={} arr={:.6f},{:.6f}".format(jid, loc[0], loc[1]))
            return OK, {"journey_id": jid, "arrival_stamped": True, "lat": loc[0], "lon": loc[1]}
        print("[JOURNEY] arrived id={} (no GPS fix — set_arrival unchanged)".format(jid))
        return OK, {"journey_id": jid, "arrival_stamped": False}
    except Exception as e:
        print("[JOURNEY] end err:", repr(e))
        return ERR_INTERNAL, {}


# ---------------------------------------------------------------- destination

def _valid_pair(val):
    if isinstance(val, (list, tuple)) and len(val) == 2:
        try:
            la = float(val[0]); lo = float(val[1])
            if -90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0:
                return (la, lo)
        except Exception:
            pass
    return None


def dest_set_here(gps=None):
    """Set the operator target to the turtle's own current position
    (never the phone's).

    OK              info: {"lat", "lon"}
    ERR_NO_GPS_FIX  no fix
    """
    try:
        loc = current_fix(gps)
        if loc is None:
            print("[DEST] set_destination: no GPS fix")
            return ERR_NO_GPS_FIX, {}
        save_set_fields({
            "set_destination": [loc[0], loc[1]],
            "set_short_name": "SET",
            "set_full_name": "User Set",
        })
        print("[DEST] set_destination = {:.6f},{:.6f}".format(loc[0], loc[1]))
        return OK, {"lat": loc[0], "lon": loc[1]}
    except Exception as e:
        print("[DEST] set here err:", repr(e))
        return ERR_INTERNAL, {}


def dest_set_mission():
    """Copy the grand-mission target into the operator target.

    OK              info: {"lat", "lon", "name"}
    ERR_NO_TARGET   no valid mission_destination stored
    """
    try:
        from config import load_config
        c = load_config() or {}
        md = _valid_pair(c.get("mission_destination"))
        if md is None:
            print("[DEST] mission target: none stored")
            return ERR_NO_TARGET, {}
        name = str(c.get("mission_dest_full_name") or "")
        save_set_fields({
            "set_destination": [md[0], md[1]],
            "set_short_name": str(c.get("mission_dest_short_name") or ""),
            "set_full_name": name,
        })
        print("[DEST] set_destination = mission {:.6f},{:.6f}".format(md[0], md[1]))
        return OK, {"lat": md[0], "lon": md[1], "name": name}
    except Exception as e:
        print("[DEST] set mission err:", repr(e))
        return ERR_INTERNAL, {}


def dest_set_coords(lat, lon):
    """Set the operator target to explicit coordinates (app map picker;
    no OLED equivalent).

    OK              info: {"lat", "lon"}
    ERR_BAD_VALUE   not a valid lat/lon
    """
    try:
        p = _valid_pair((lat, lon))
        if p is None:
            return ERR_BAD_VALUE, {}
        save_set_fields({
            "set_destination": [p[0], p[1]],
            "set_short_name": "SET",
            "set_full_name": "User Set",
        })
        print("[DEST] set_destination = {:.6f},{:.6f} (coords)".format(p[0], p[1]))
        return OK, {"lat": p[0], "lon": p[1]}
    except Exception as e:
        print("[DEST] set coords err:", repr(e))
        return ERR_INTERNAL, {}


def dest_clear():
    """Clear the operator target; navigation falls back to the mission
    (WaypointSequencer order). App-only; no OLED equivalent.

    OK
    """
    try:
        save_set_fields({
            "set_destination": None,
            "set_short_name": "",
            "set_full_name": "",
        })
        print("[DEST] set_destination cleared")
        return OK, {}
    except Exception as e:
        print("[DEST] clear err:", repr(e))
        return ERR_INTERNAL, {}


# ---------------------------------------------------------------- GPS & logging

# NOT_STAMPED reasons (GATT contract v1, GPS_STAMP result payload)
STAMP_RTC_NOT_EPOCH = 1     # no synced clock — a record with no timestamp is useless
STAMP_GPS_DISABLED = 2      # GPS off and no sensor values to record instead
STAMP_NO_FIX = 3            # GPS on but no fix, and no sensor values either
STAMP_OTHER = 0xFF          # e.g. a sensor sample still in flight — see serial

# Stamps taken since boot (contract Status → stamps_session).
_stamps_session = 0


def stamps_session():
    return _stamps_session


def _rtc_synced():
    try:
        from machine import RTC
        return RTC().datetime()[0] >= 2020
    except Exception:
        return False


def gps_stamp(telemetry, cfg=None):
    """Take one hand-stamped telemetry reading (manual logging mode).

    Never blocks on the network: the scheduler commits the payload before
    returning — handed to the background sender, or written to the flash
    queue when it can't be. A later shore delivery shows up in the queue
    count / last-sent time, not as a second result.

    OK               info: {"committed_to": 0 flash queue | 1 sender,
                            "stamps_session", "has_position"}
    ERR_WRONG_STATE  telemetry_mode isn't "manual"
    ERR_NOT_STAMPED  info: {"reason": STAMP_*} — nothing was recorded
    ERR_INTERNAL     no telemetry scheduler
    """
    global _stamps_session
    try:
        if not isinstance(cfg, dict):
            from config import load_config
            cfg = load_config() or {}
        mode = str(cfg.get("telemetry_mode", "auto") or "auto").strip().lower()
        if mode != "manual":
            return ERR_WRONG_STATE, {"telemetry_mode": mode}
        if telemetry is None:
            print("[GPS] stamp: no telemetry scheduler")
            return ERR_INTERNAL, {}

        if not telemetry.send_manual():
            print("[GPS] stamp: scheduler would not arm")
            return ERR_INTERNAL, {}

        has_position = current_fix(None) is not None
        ret = telemetry.tick(cfg)

        # The scheduler consumes the manual flag only when it builds a payload;
        # a flag still set means nothing was recorded.
        if telemetry.manual_pending():
            telemetry.clear_manual()
            if not _rtc_synced():
                reason = STAMP_RTC_NOT_EPOCH
            elif not has_position and not cfg.get("gps_enabled", False):
                reason = STAMP_GPS_DISABLED
            elif not has_position:
                reason = STAMP_NO_FIX
            else:
                reason = STAMP_OTHER
            print("[GPS] stamp: not stamped (reason {})".format(reason))
            return ERR_NOT_STAMPED, {"reason": reason}

        _stamps_session += 1
        committed_to = 0 if ret is False else 1
        print("[GPS] stamp #{} -> {}{}".format(
            _stamps_session, "flash queue" if committed_to == 0 else "sender",
            "" if has_position else " (no position)"))
        return OK, {"committed_to": committed_to,
                    "stamps_session": _stamps_session,
                    "has_position": has_position}
    except Exception as e:
        print("[GPS] stamp err:", repr(e))
        return ERR_INTERNAL, {}


def gps_set_enabled(gps, on):
    """Persist gps_enabled and, where the driver supports it, power the
    module on or off.

    The current L76K driver (src/sensors/xiao_gnss.py GnssModule) has no
    power control, so today this only changes the flag: the module keeps
    running and NavController keeps reading it. info["power_control"] says
    which happened.

    OK               info: {"enabled", "power_control"}
    ERR_NO_HARDWARE  no GPS session exists (module absent at boot)
    """
    try:
        if gps is None:
            return ERR_NO_HARDWARE, {}
        on = bool(on)
        if write_config({"gps_enabled": on}) is None:
            return ERR_INTERNAL, {}
        fn = getattr(gps, "enable" if on else "disable", None)
        power_control = fn is not None
        if power_control:
            try:
                fn()
            except Exception as e:
                print("[GPS] enable/disable err:", repr(e))
                power_control = False
        print("[GPS] gps_enabled = {}{}".format(
            on, "" if power_control else " (flag only - driver has no power control)"))
        return OK, {"enabled": on, "power_control": power_control}
    except Exception as e:
        print("[GPS] set enabled err:", repr(e))
        return ERR_INTERNAL, {}


TELEMETRY_MODES = ("off", "auto", "manual")


def telemetry_set_mode(mode):
    """Set telemetry_mode (authoritative; telemetry_enabled is rebuilt from it).

    OK             info: {"mode"}
    ERR_BAD_VALUE  not one of off / auto / manual
    """
    try:
        mode = str(mode or "").strip().lower()
        if mode not in TELEMETRY_MODES:
            return ERR_BAD_VALUE, {}
        if write_config({"telemetry_mode": mode}) is None:
            return ERR_INTERNAL, {}
        print("[TELEMETRY] mode =", mode)
        return OK, {"mode": mode}
    except Exception as e:
        print("[TELEMETRY] set mode err:", repr(e))
        return ERR_INTERNAL, {}


def telemetry_set_interval(seconds):
    """Set telemetry_post_every_s (App-only; no OLED writer).

    OK             info: {"seconds"}
    ERR_BAD_VALUE  below the 10 s floor, or not a number
    """
    try:
        try:
            s = int(seconds)
        except Exception:
            return ERR_BAD_VALUE, {}
        if s < 10:
            return ERR_BAD_VALUE, {}
        if write_config({"telemetry_post_every_s": s}) is None:
            return ERR_INTERNAL, {}
        print("[TELEMETRY] interval =", s, "s")
        return OK, {"seconds": s}
    except Exception as e:
        print("[TELEMETRY] set interval err:", repr(e))
        return ERR_INTERNAL, {}


def api_handshake(telemetry, cfg=None):
    """Ask the scheduler to send now, as the Online screen does on entry.
    The send runs in the background; poll api_outcome() for the result.

    IN_PROGRESS      info: {"before_ms"} — pass it to api_outcome()
    ERR_WRONG_STATE  telemetry_mode is "off"
    ERR_INTERNAL     no telemetry scheduler
    """
    try:
        if not isinstance(cfg, dict):
            from config import load_config
            cfg = load_config() or {}
        if str(cfg.get("telemetry_mode", "auto")).strip().lower() == "off":
            return ERR_WRONG_STATE, {}
        if telemetry is None:
            return ERR_INTERNAL, {}
        before_ms = None
        try:
            before_ms = telemetry.api_state.get("last_ms")
        except Exception:
            pass
        telemetry.request_now()
        return IN_PROGRESS, {"before_ms": before_ms}
    except Exception as e:
        print("[ONLINE] handshake err:", repr(e))
        return ERR_INTERNAL, {}


# ---------------------------------------------------------------- connectivity

def wifi_set_enabled(on, wifi=None):
    """Persist wifi_enabled; turning off also drops the radio when a
    WiFiManager is given. Turning on only persists — the connect attempt
    is the caller's (the WiFi screen animates its own; the Bluetooth
    handler will poll one in Phase 6).

    OK  info: {"enabled"}
    """
    try:
        on = bool(on)
        if write_config({"wifi_enabled": on}) is None:
            return ERR_INTERNAL, {}
        if not on and wifi is not None:
            try:
                wifi.disconnect()
            except Exception:
                pass
            try:
                wifi.active(False)
            except Exception:
                pass
        print("[WIFI] wifi_enabled =", on)
        return OK, {"enabled": on}
    except Exception as e:
        print("[WIFI] set enabled err:", repr(e))
        return ERR_INTERNAL, {}


def wifi_set_credentials(ssid, password):
    """Save WiFi credentials (App-only). Saving never depends on a connect
    succeeding; the caller reconnects if wifi_enabled.

    OK             info: {"ssid"}
    ERR_BAD_VALUE  empty SSID, SSID > 32 bytes or password > 63 bytes
    """
    try:
        ssid = str(ssid or "").strip()
        password = str(password or "")
        if not ssid or len(ssid.encode()) > 32 or len(password.encode()) > 63:
            return ERR_BAD_VALUE, {}
        if write_config({"wifi_ssid": ssid, "wifi_password": password}) is None:
            return ERR_INTERNAL, {}
        print("[WIFI] credentials saved for %r" % ssid)
        return OK, {"ssid": ssid}
    except Exception as e:
        print("[WIFI] set credentials err:", repr(e))
        return ERR_INTERNAL, {}


CONNECTION_MODES = ("wifi_auto", "wifi_manual")   # "lora" is reserved


def connection_mode_set(mode):
    """Set mission_connection_mode (App-only).

    OK             info: {"mode"}
    ERR_BAD_VALUE  not wifi_auto / wifi_manual
    """
    try:
        mode = str(mode or "").strip().lower()
        if mode not in CONNECTION_MODES:
            return ERR_BAD_VALUE, {}
        if write_config({"mission_connection_mode": mode}) is None:
            return ERR_INTERNAL, {}
        print("[WIFI] mission_connection_mode =", mode)
        return OK, {"mode": mode}
    except Exception as e:
        print("[WIFI] set connection mode err:", repr(e))
        return ERR_INTERNAL, {}


# ---------------------------------------------------------------- sail & compass

def nav_luff_sweep(nav):
    """Start the autonomous luff sweep (NavController, non-blocking).
    Poll nav.sweeping() / nav.wind_angle() for the outcome.

    IN_PROGRESS      sweep started
    ERR_BUSY         a sweep is already running
    ERR_WRONG_STATE  info: {"state"} — only valid in ACQUIRE / SAIL_NAV
    ERR_NO_HARDWARE  no NavController (not turtle mode)
    """
    try:
        if nav is None:
            return ERR_NO_HARDWARE, {}
        if nav.sweeping():
            return ERR_BUSY, {}
        if nav.begin_luff_sweep():
            print("[NAV] luff sweep started")
            return IN_PROGRESS, {}
        state = None
        try:
            from src.nav import state_machine as sm
            state = sm.get_state()
        except Exception:
            pass
        print("[NAV] luff sweep refused in state", state)
        return ERR_WRONG_STATE, {"state": state}
    except Exception as e:
        print("[NAV] luff sweep err:", repr(e))
        return ERR_INTERNAL, {}


def compass_set_offset(deg, heading=None):
    """Set compass_offset_deg (App-only; no OLED writer). Pass the shared
    HeadingSource (nav.heading_source()) to apply it immediately; otherwise
    it takes effect on the next boot.

    OK             info: {"deg"}
    ERR_BAD_VALUE  outside -180..180
    """
    try:
        try:
            d = int(deg)
        except Exception:
            return ERR_BAD_VALUE, {}
        if not -180 <= d <= 180:
            return ERR_BAD_VALUE, {}
        if write_config({"compass_offset_deg": d}) is None:
            return ERR_INTERNAL, {}
        if heading is not None:
            try:
                heading.set_offset(d)
            except Exception as e:
                print("[COMPASS] live offset apply failed:", repr(e))
        print("[COMPASS] offset =", d)
        return OK, {"deg": d}
    except Exception as e:
        print("[COMPASS] set offset err:", repr(e))
        return ERR_INTERNAL, {}


# ---------------------------------------------------------------- system

def set_turtle_mode(on):
    """Persist turtle_mode (turtleOS <-> airOS). Takes effect on the next
    boot; the caller reboots after reporting (CLAUDE.md gotcha 15 — no
    hot-reload).

    OK  info: {"turtle_mode"}
    """
    try:
        on = bool(on)
        if write_config({"turtle_mode": on}) is None:
            return ERR_INTERNAL, {}
        print("[SYSTEM] turtle_mode =", on, "(reboot to apply)")
        return OK, {"turtle_mode": on}
    except Exception as e:
        print("[SYSTEM] set turtle_mode err:", repr(e))
        return ERR_INTERNAL, {}


def api_outcome(telemetry, before_ms):
    """Final result of an api_handshake(), or None while it is still running:
    OK or ERR_API_FAILED once the scheduler records a new attempt."""
    try:
        st = telemetry.api_state
        if st.get("sending") or st.get("last_ms") == before_ms or st.get("ok") is None:
            return None
        return OK if st.get("ok") else ERR_API_FAILED
    except Exception:
        return None
