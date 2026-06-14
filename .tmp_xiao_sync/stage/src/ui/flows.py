# src/ui/flows.py — Screen flow logic for AirBuddy
#
# PATCH (Feb 2026):
# - Fix DeviceScreen.show_live(...) call signature (uses api_info, not api_boot/wifi_boot)
# - Fetch + normalize device assignment info from /api/v1/device (Pico-safe)
# - Prevent "post-screen reset_and_flush()" from eating the next click:
#   use a short post-screen flush WITHOUT btn.reset()
# - Make WiFiScreen call match its signature: show_live(btn)
# - Make OnlineScreen call match its signature: show_live(btn)
# - Make LoggingScreen call match its signature: show_live(btn, get_queue_size=None, get_last_sent=None)
# - NEW: After Logging, route to GPS screen ONLY if GPS connectivity is present; otherwise return to waiting.
#
# PATCH (Feb 2026 - Offline carousel + quad fix):
# - Connectivity carousel now works OFFLINE (no early return when wifi/api missing)
# - Quad click works reliably OFFLINE (prevents the "third click becomes next click" bug)
#   by settling/releasing + short flushing at the start of the carousel and before the first wait.
# - Offline status notices are non-blocking (brief dwell OR user click), then carousel continues.

import time
from src.ui.clicks import (
    draw_text,
    wait_for_single,
    wait_release,
    dwell_or_click,
    reset_and_flush,
    gc_collect,
)


# ------------------------------------------------------------
# Small helpers (Pico-safe, low import overhead)
# ------------------------------------------------------------
def _gc():
    try:
        import gc
        gc.collect()
    except Exception:
        pass


def _json():
    try:
        import ujson as j
        return j
    except Exception:
        import json as j
        return j


def _post_screen_flush(btn, ms=90, poll_ms=25):
    """
    Very short drain to remove bounce, but not long enough to eat a real click.
    IMPORTANT: Do NOT call btn.reset() here.
    """
    if btn is None:
        return
    try:
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < int(ms):
            try:
                btn.poll_action()
            except Exception:
                pass
            time.sleep_ms(int(poll_ms))
    except Exception:
        pass


def _entry_settle(btn, poll_ms=25):
    """
    Called right when we ENTER a flow triggered by a multi-click.
    Prevents the tail of the triggering click from being interpreted as the
    "next click" inside the flow (this is what was breaking quad offline).
    """
    try:
        wait_release(btn)
    except Exception:
        pass
    _post_screen_flush(btn, ms=140, poll_ms=poll_ms)


def _draw_center_lines(oled, lines, y0=18, line_h=12):
    """
    Minimal multiline centered text renderer.
    """
    if oled is None:
        return
    fb = getattr(oled, "oled", None)
    if fb is None:
        return

    try:
        fb.fill(0)
    except Exception:
        return

    writer = getattr(oled, "f_med", None) or getattr(oled, "f_small", None)
    if writer is None:
        try:
            fb.show()
        except Exception:
            pass
        return

    ow = int(getattr(oled, "width", 128))

    y = int(y0)
    for s in (lines or []):
        s = str(s)
        try:
            w, _ = writer.size(s)
            x = max(0, (ow - int(w)) // 2)
        except Exception:
            x = 0
        try:
            writer.write(s, x, y)
        except Exception:
            pass
        y += int(line_h)

    try:
        fb.show()
    except Exception:
        pass

    _gc()


def _fetch_device_info(cfg, tick_cb=None, wifi=None):
    """
    Low-mem GET to fetch device info for DeviceScreen.
    Normalizes your server JSON into flat keys:
      device_name, home_name, room_name, community_name
    Returns dict (possibly empty).
    tick_cb: optional no-arg callable invoked before each URL attempt (for animations).
    """
    if not isinstance(cfg, dict):
        return {}

    api_base = (cfg.get("api_base") or "").strip().rstrip("/")
    device_id = (cfg.get("device_id") or "").strip()
    device_key = (cfg.get("device_key") or "").strip()

    if (not api_base) or (not device_id) or (not device_key):
        print("[DEVICE] fetch: missing config (api_base={}, id={})".format(
            bool(api_base), bool(device_id)))
        return {}

    try:
        import urequests as requests
    except Exception:
        print("[DEVICE] fetch: urequests import failed")
        return {}

    headers = {
        "X-Device-Id": device_id,
        "X-Device-Key": device_key,
    }

    # Single URL — fallbacks fragment the heap without adding recovery value
    urls = (
        api_base + "/api/v1/device?compact=1",
    )

    # Re-assert WiFi PM_PERFORMANCE — same guard the telemetry scheduler uses.
    # WiFi PM can silently downgrade between boot and carousel, causing OSError(-202).
    if wifi is not None:
        try:
            wifi._apply_pm_performance(quiet=True)
        except Exception:
            pass

    j = _json()

    def _normalize(data):
        if not isinstance(data, dict):
            return {}

        dev = data.get("device") if isinstance(data.get("device"), dict) else {}
        asg = data.get("assignment") if isinstance(data.get("assignment"), dict) else {}

        home = asg.get("home") if isinstance(asg.get("home"), dict) else {}
        room = asg.get("room") if isinstance(asg.get("room"), dict) else {}
        com = asg.get("community") if isinstance(asg.get("community"), dict) else {}

        out = {}

        dn = dev.get("device_name") if isinstance(dev, dict) else None
        if dn is None:
            dn = data.get("device_name")
        if dn is not None:
            out["device_name"] = dn

        hn = home.get("home_name") if isinstance(home, dict) else None
        if hn is None:
            hn = data.get("home_name")
        if hn is not None:
            out["home_name"] = hn

        rn = room.get("room_name") if isinstance(room, dict) else None
        if rn is None:
            rn = data.get("room_name")
        if rn is not None:
            out["room_name"] = rn

        cn = com.get("com_name") if isinstance(com, dict) else None
        if cn is None:
            cn = data.get("community_name")
        if cn is None:
            cn = data.get("com_name")
        if cn is not None:
            out["community_name"] = cn

        return out

    try:
        import gc as _gc_d
        print("[DEVICE] heap: %d KB free" % (_gc_d.mem_free() // 1024))
    except Exception:
        pass

    for url in urls:
        r = None
        try:
            if tick_cb:
                try:
                    tick_cb()
                except Exception:
                    pass
            _gc()
            print("[DEVICE] GET", url)
            r = requests.get(url, headers=headers, timeout=8)
            code = getattr(r, "status_code", None)
            print("[DEVICE] HTTP", code)

            if code is None or int(code) < 200 or int(code) >= 300:
                try:
                    r.close()
                except Exception:
                    pass
                r = None
                continue

            try:
                txt = r.text
            except Exception:
                txt = None

            try:
                r.close()
            except Exception:
                pass
            r = None
            _gc()

            if not txt:
                print("[DEVICE] empty body")
                return {}

            try:
                data = j.loads(txt)
            except Exception:
                print("[DEVICE] JSON parse fail")
                return {}

            out = _normalize(data)
            if out:
                print("[DEVICE] OK name=", out.get("device_name"),
                      "home=", out.get("home_name"))
            return out if out else (data if isinstance(data, dict) else {})

        except Exception as _e:
            print("[DEVICE] ERR", repr(_e))
            try:
                if r:
                    r.close()
            except Exception:
                pass
            _gc()
            continue

    print("[DEVICE] all URLs failed")
    return {}


def _show_frowny(oled, btn, line1, line2):
    """
    Show FrownyScreen with two message lines and wait for any button click.
    Falls back to a plain centered text + brief sleep if the import fails.
    """
    try:
        from src.ui.screens.frowny import FrownyScreen
        FrownyScreen(oled).show(btn, line1=line1, line2=line2)
    except Exception:
        _draw_center_lines(oled, [line1, line2], y0=22, line_h=12)
        try:
            time.sleep_ms(2000)
        except Exception:
            pass


def _log_screen(name, info=None, err=None):
    """Print a one-line screen-entry log.  Called once at landing, never during live updates."""
    if err:
        print("[SCREEN] {}  ERROR: {}".format(name, err))
    elif info:
        print("[SCREEN] {}  {}".format(name, info))
    else:
        print("[SCREEN] {}".format(name))


def _offline_notice(oled, btn, lines, dwell_ms=1200, poll_ms=25):
    """
    Show a brief status notice.
    - Returns an action if the user clicks (including quad/debug).
    - Returns None if it times out.
    """
    _draw_center_lines(oled, lines, y0=18, line_h=12)
    try:
        return dwell_or_click(btn, dwell_ms=int(dwell_ms), poll_ms=poll_ms)
    except Exception:
        try:
            time.sleep_ms(int(dwell_ms))
        except Exception:
            pass
        return None


def connectivity_carousel(
        btn,
        oled,
        status,
        cfg,
        wifi,
        api_boot,
        wifi_boot,
        gps,
        get_screen,
        selfdestruct_cb=None,
        flush_ms=250,
        poll_ms=25,
        tick_fn=None,
        telemetry=None,
        device_info=None,
):
    """
    Triple-click flow (carousel order):

    Waiting
       ↓ (triple)
    WiFi Screen (ALWAYS)
       ↓ if WiFi OK
    Online Screen (ONLY if WiFi OK)
       ↓ single click always advances
    Telemetry Screen
       ↓ if Logging enabled/ON
    Device Screen (ONLY if Logging enabled)
       ↓ (any non-single exits)
    Waiting

    Rules:
    - NO offline notices (removed).
    - Quad/debug handled at every step.
    """

    # ---- settle tail of triggering triple-click ----
    _entry_settle(btn, poll_ms=poll_ms)

    # Helpers to standardize "exit" behavior
    def _handle_special(a):
        if a == "quad":
            if selfdestruct_cb:
                selfdestruct_cb()
                _post_screen_flush(btn, ms=120, poll_ms=poll_ms)
                return "handled"
            _post_screen_flush(btn, ms=120, poll_ms=poll_ms)
            return "quad"
        if a == "debug":
            _post_screen_flush(btn, ms=120, poll_ms=poll_ms)
            return "debug"
        return None

    def _exit(a=None):
        _post_screen_flush(btn, ms=120, poll_ms=poll_ms)
        return a

    # ------------------------------------------------------------
    # 1) WIFI SCREEN — always shown; disabled state handled by the screen itself
    # ------------------------------------------------------------
    try:
        _w_ssid = (cfg or {}).get("wifi_ssid", "") or ""
        _w_ok   = bool((status or {}).get("wifi_ok", False))
        _log_screen("wifi", "ssid={}  connected={}".format(_w_ssid or "?", _w_ok))
    except Exception:
        _log_screen("wifi")
    wifi_scr = get_screen("wifi")
    if wifi_scr and hasattr(wifi_scr, "show_live"):
        try:
            a = wifi_scr.show_live(btn, tick_fn=tick_fn)
        except Exception:
            a = wait_for_single(btn, tick_fn=tick_fn)
    else:
        draw_text(oled, "WIFI", y=24)
        a = wait_for_single(btn, tick_fn=tick_fn)

    sp = _handle_special(a)
    if sp == "handled":
        return
    if sp in ("quad", "debug"):
        return sp

    # Anything but single => exit back to waiting
    if a != "single":
        return _exit(a)

    # After WiFi screen, require WiFi to be actually connected / on
    try:
        wifi_ok = bool(status.get("wifi_ok"))
    except Exception:
        wifi_ok = False

    if not wifi_ok:
        return _exit(None)

    _post_screen_flush(btn, ms=120, poll_ms=poll_ms)

    # ------------------------------------------------------------
    # 2) ONLINE/API SCREEN (only if WiFi OK)
    # ------------------------------------------------------------
    try:
        _api_ok = bool((status or {}).get("api_ok", False))
        try:
            from src.app.telemetry_state import TelemetryState as _TS
            _q = _TS.get_queue_size()
        except Exception:
            _q = "?"
        _log_screen("online", "api_ok={}  queue={}".format(_api_ok, _q))
    except Exception:
        _log_screen("online")
    online_scr = get_screen("online")
    if online_scr and hasattr(online_scr, "show_live"):
        try:
            a = online_scr.show_live(
                btn,
                wifi_ok=wifi_ok,
                gps_state=status.get("gps_on") if isinstance(status, dict) else None,
                tick_fn=tick_fn,
                telemetry=telemetry,
            )
        except Exception:
            a = wait_for_single(btn, tick_fn=tick_fn)
    else:
        draw_text(oled, "ONLINE", y=24)
        a = wait_for_single(btn, tick_fn=tick_fn)

    sp = _handle_special(a)
    if sp == "handled":
        return
    if sp in ("quad", "debug"):
        return sp

    if a != "single":
        return _exit(a)

    _post_screen_flush(btn, ms=120, poll_ms=poll_ms)

    # ------------------------------------------------------------
    # 3) DEVICE SCREEN — use boot-time cached info (no in-carousel HTTP).
    #    The heap at this point (~19 KB) is too fragmented for a reliable
    #    TCP connection.  Boot already fetched this info successfully.
    # ------------------------------------------------------------
    try:
        _dn = (device_info or {}).get("device_name", "?") if isinstance(device_info, dict) else "?"
        _hn = (device_info or {}).get("home_name", "?") if isinstance(device_info, dict) else "?"
        _log_screen("device", "name={}  home={}".format(_dn, _hn))
    except Exception:
        _log_screen("device")
    device_scr = get_screen("device")
    if device_scr and hasattr(device_scr, "show_live"):
        try:
            # Use boot-time device info if available; skip HTTP entirely.
            _use_cached = (
                isinstance(device_info, dict)
                and device_info.get("ok")
                and device_info.get("device_name")
            )
            if _use_cached:
                api_info = device_info
                print("[DEVICE] using boot cache:", device_info.get("device_name"))
                try:
                    from src.ui import connection_header as _ch_dev
                    _ch_dev.set_api_ok(True)
                except Exception:
                    pass
            else:
                # Fall back to a single fresh fetch (WiFi PM already asserted above)
                _gc(); _gc()
                api_info = _fetch_device_info(cfg, tick_cb=None, wifi=wifi)
                try:
                    from src.ui import connection_header as _ch_dev
                    _ch_dev.set_api_ok(bool(api_info))
                except Exception:
                    pass
            a = device_scr.show_live(btn=btn, api_info=api_info, tick_fn=tick_fn)
        except Exception:
            a = wait_for_single(btn, tick_fn=tick_fn)
    else:
        draw_text(oled, "DEVICE", y=24)
        a = wait_for_single(btn, tick_fn=tick_fn)

    sp = _handle_special(a)
    if sp == "handled":
        return
    if sp in ("quad", "debug"):
        return sp

    if a != "single":
        return _exit(a)

    _post_screen_flush(btn, ms=120, poll_ms=poll_ms)

    # ------------------------------------------------------------
    # 4) LOGGING SCREEN
    # ------------------------------------------------------------
    try:
        _tel_en = bool((cfg or {}).get("telemetry_enabled", False))
        try:
            from src.app.telemetry_state import TelemetryState as _TS2
            _q2 = _TS2.get_queue_size()
        except Exception:
            _q2 = "?"
        _log_screen("logging", "enabled={}  queue={}".format(_tel_en, _q2))
    except Exception:
        _log_screen("logging")
    log_scr = get_screen("logging")
    if log_scr and hasattr(log_scr, "show_live"):
        try:
            a = log_scr.show_live(btn, tick_fn=tick_fn)
        except Exception:
            a = wait_for_single(btn, tick_fn=tick_fn)
    else:
        draw_text(oled, "LOGGING", y=24)
        a = wait_for_single(btn, tick_fn=tick_fn)

    sp = _handle_special(a)
    if sp == "handled":
        return
    if sp in ("quad", "debug"):
        return sp

    if a != "single":
        return _exit(a)

    _post_screen_flush(btn, ms=120, poll_ms=poll_ms)

    # ------------------------------------------------------------
    # 5) GPS SCREEN — last in the carousel; reads UART only, no TCP,
    #    so it can tolerate a tighter / more-fragmented heap.
    # ------------------------------------------------------------
    try:
        _gps_on = (status or {}).get("gps_on", 0)
        _gps_en = bool((cfg or {}).get("gps_enabled", False))
        _gps_lbl = ("none", "init", "fixed")
        _gps_state_str = _gps_lbl[_gps_on] if isinstance(_gps_on, int) and 0 <= _gps_on <= 2 else str(_gps_on)
        _log_screen("gps", "enabled={}  state={}".format(_gps_en, _gps_state_str))
    except Exception:
        _log_screen("gps")
    gps_scr = get_screen("gps")
    if gps_scr and hasattr(gps_scr, "show_live"):
        try:
            a = gps_scr.show_live(gps, btn)
        except Exception:
            a = wait_for_single(btn, tick_fn=tick_fn)
    else:
        draw_text(oled, "GPS", y=24)
        a = wait_for_single(btn, tick_fn=tick_fn)

    sp = _handle_special(a)
    if sp == "handled":
        return
    if sp in ("quad", "debug"):
        return sp

    return _exit(None)

# ============================================================
# SENSOR CAROUSEL (SINGLE CLICK)
# ============================================================
def sensor_carousel(
        btn,
        oled,
        air,
        get_screen,
        dwell_ms=4000,
        flush_ms=250,
        poll_ms=25,
        tick_fn=None,
        gps=None,
        cfg=None,
):
    if air is None:
        print("[SINGLE] air=None — no sensors available")
        _show_frowny(oled, btn, "Ack! No sensors", "are connected!")
        return

    # One full reading for CO2/TVOC screens
    try:
        reading = air.finish_sampling(log=False)
    except Exception as e:
        print("[FLOW] finish_sampling error:", repr(e))
        return

    gc_collect()

    # Determine which sensors are present after hardware init (done inside finish_sampling)
    _has_scd41 = getattr(air, '_scd41', None) is not None
    _has_ens = getattr(air, '_ens', None) is not None
    _has_aht = getattr(air, '_aht', None) is not None
    _has_aht10 = getattr(air, '_aht10', None) is not None
    _has_bme = getattr(air, '_bme', None) is not None

    # Build the ordered screen list for this carousel run.
    # Only include a screen if the required sensor is actually connected.
    _sensor_screens = []
    if _has_scd41:
        _sensor_screens.append("co2")       # SCD4X CO2 screen
    if _has_ens:
        _sensor_screens.append("eco2")      # ENS160 eCO2 screen
        _sensor_screens.append("tvoc")      # ENS160 TVOC screen
    if _has_aht or _has_bme:
        _sensor_screens.append("temp")      # temp screen: AHT21, BME280, or both
    if _has_scd41:
        _sensor_screens.append("temp2")     # SCD4X temperature screen

    _turtle_mode = bool((cfg or {}).get("turtle_mode", False))

    _all_screens = (["sailpoint", "servo", "gps", "destination"] if _turtle_mode else []) \
                   + _sensor_screens \
                   + (["summary"] if _sensor_screens else [])
    print("[SINGLE] screens:", _all_screens if _all_screens else "none")

    # Preload ALL carousel screens now, while the heap is clean.
    # If _bg_tick fires telemetry during a dwell, get_screen() will return
    # the cached instance without needing a 1280-byte module bytecode allocation.
    _preload = (["sailpoint", "servo", "gps", "destination"] if _turtle_mode else []) + _sensor_screens + ["summary"]
    for _n in _preload:
        get_screen(_n)
        _gc()
    reset_and_flush(btn, flush_ms, poll_ms)

    if _turtle_mode:
        # ---- SAILPOINT (first in single-click carousel, turtle mode only) ----
        _gc()
        compass_scr = get_screen("sailpoint")
        try:
            _sp_angle = compass_scr._read() if compass_scr else None
            if _sp_angle is not None:
                _log_screen("sailpoint", "angle={:.1f} deg".format(_sp_angle))
            else:
                _log_screen("sailpoint", err="AS5600 not connected")
        except Exception:
            _log_screen("sailpoint")
        if compass_scr and hasattr(compass_scr, "show_live"):
            try:
                a = compass_scr.show_live(btn, tick_fn=tick_fn)
            except Exception:
                a = None
        else:
            draw_text(oled, "Sailpoint", y=24)
            a = wait_for_single(btn, tick_fn=tick_fn)

        if a not in ("single", None):
            reset_and_flush(btn, flush_ms, poll_ms)
            return a
        _post_screen_flush(btn, ms=120, poll_ms=poll_ms)

        # ---- SERVO (second in single-click carousel, turtle mode only) ----
        _gc()
        servo_scr = get_screen("servo")
        try:
            _servo_cfg = bool((cfg or {}).get("servo_present", False))
            _log_screen("servo", "configured={}".format(_servo_cfg))
        except Exception:
            _log_screen("servo")
        if servo_scr and hasattr(servo_scr, "show_live"):
            try:
                a = servo_scr.show_live(btn)
            except Exception:
                a = None
        else:
            draw_text(oled, "Servo", y=24)
            a = wait_for_single(btn, tick_fn=tick_fn)

        if a not in ("single", None):
            reset_and_flush(btn, flush_ms, poll_ms)
            return a
        _post_screen_flush(btn, ms=120, poll_ms=poll_ms)

        # ---- GPS (third in single-click carousel, turtle mode only) ----
        _gc()
        gps_scr = get_screen("gps")
        try:
            try:
                from src.ui import connection_header as _ch_gps
                _gs = _ch_gps.get_gps_state()
                _gps_lbl2 = ("none", "init", "fixed")
                _gs_str = _gps_lbl2[_gs] if isinstance(_gs, int) and 0 <= _gs <= 2 else str(_gs)
            except Exception:
                _gs_str = "?"
            _log_screen("gps", "state={}".format(_gs_str))
        except Exception:
            _log_screen("gps")
        if gps_scr and hasattr(gps_scr, "show_live"):
            try:
                a = gps_scr.show_live(gps, btn)
            except Exception:
                a = None
        else:
            draw_text(oled, "GPS", y=24)
            a = wait_for_single(btn, tick_fn=tick_fn)

        if a not in ("single", None):
            reset_and_flush(btn, flush_ms, poll_ms)
            return a
        _post_screen_flush(btn, ms=120, poll_ms=poll_ms)

        # ---- DESTINATION (fourth in single-click carousel, turtle mode only) ----
        _gc()
        dest_scr = get_screen("destination")
        try:
            _d_name = str((cfg or {}).get("dest_name", "") or "")
            _d_coord = (cfg or {}).get("dest_coord") or [None, None]
            _d_lat = _d_coord[0] if isinstance(_d_coord, (list, tuple)) and len(_d_coord) > 0 else None
            _d_lon = _d_coord[1] if isinstance(_d_coord, (list, tuple)) and len(_d_coord) > 1 else None
            if _d_name or _d_lat is not None:
                _log_screen("destination", "name='{}' lat={} lon={}".format(_d_name, _d_lat, _d_lon))
            else:
                _log_screen("destination", "no destination set")
        except Exception:
            _log_screen("destination")
        if dest_scr and hasattr(dest_scr, "show_live"):
            try:
                a = dest_scr.show_live(btn, tick_fn=tick_fn)
            except Exception:
                a = None
        else:
            draw_text(oled, "Destination", y=24)
            a = wait_for_single(btn, tick_fn=tick_fn)

        if a not in ("single", None):
            reset_and_flush(btn, flush_ms, poll_ms)
            return a
        _post_screen_flush(btn, ms=120, poll_ms=poll_ms)

    for name in _sensor_screens:
        _gc()
        scr = get_screen(name)
        if not scr:
            print("[FLOW] screen missing:", name)
            continue

        try:
            if name == "co2":
                _v = getattr(reading, "scd41_co2_ppm", None)
                if _v is not None:
                    _log_screen("co2", "scd41_co2={}ppm".format(_v))
                else:
                    _log_screen("co2", err="no SCD41 reading")
            elif name == "eco2":
                _log_screen("eco2", "eco2={}ppm".format(getattr(reading, "eco2_ppm", "?")))
            elif name == "tvoc":
                _log_screen("tvoc", "tvoc={}ppb".format(getattr(reading, "tvoc_ppb", "?")))
            elif name == "temp":
                _t = getattr(reading, "temp_c", None)
                _rh = getattr(reading, "humidity", None)
                if _t is not None:
                    _log_screen("temp", "temp={:.1f}C  rh={:.1f}%".format(float(_t), float(_rh or 0)))
                else:
                    _log_screen("temp", err="no temp reading")
            elif name == "temp2":
                _st = getattr(reading, "scd41_temp_c", None)
                if _st is not None:
                    _log_screen("temp2", "scd41_temp={:.1f}C".format(float(_st)))
                else:
                    _log_screen("temp2", err="no SCD41 temp")
            else:
                _log_screen(name)
        except Exception:
            _log_screen(name)

        try:
            # Live temp screens: run their own loop, then continue to SUMMARY
            if name in ("temp", "temp2") and hasattr(scr, "show_live"):
                try:
                    _a_temp = scr.show_live(btn=btn, air=air, tick_fn=tick_fn)
                except TypeError:
                    _a_temp = scr.show_live(btn=btn)
                if _a_temp == "sleep":
                    reset_and_flush(btn, flush_ms, poll_ms)
                    return _a_temp

                # Use the last good reading captured by the live loop
                if getattr(air, '_last', None) is not None:
                    reading = air._last

                # If another temp screen follows in the list, continue to it;
                # otherwise flush and break to summary.
                _remaining = _sensor_screens[_sensor_screens.index(name) + 1:]
                _next_is_temp = bool(_remaining and _remaining[0] in ("temp", "temp2"))
                reset_and_flush(btn, flush_ms=min(180, flush_ms), poll_ms=poll_ms)
                if _next_is_temp:
                    continue
                break  # exit loop → go show summary

            # co2: live-updating (polls SCD41 every 5 s)
            elif name == "co2" and hasattr(scr, "show_live"):
                _captured = reading
                def _get_co2():
                    # Directly poll SCD41; update _captured in-place when new data arrives.
                    scd41 = getattr(air, '_scd41', None)
                    if scd41 is not None:
                        try:
                            result = scd41.read_if_ready()
                            if result is not None and result[0]:
                                _captured.scd41_co2_ppm = int(result[0])
                        except Exception:
                            pass
                    return _captured
                try:
                    a_live = scr.show_live(btn=btn, get_reading=_get_co2, tick_fn=tick_fn)
                except TypeError:
                    a_live = scr.show_live(btn=btn)
                reset_and_flush(btn, flush_ms=min(180, flush_ms), poll_ms=poll_ms)
                if a_live in ("single", None):
                    _gc()
                    continue
                reset_and_flush(btn, flush_ms, poll_ms)
                return a_live

            # eco2: live-updating (polls ENS160 every 5 s)
            elif name == "eco2" and hasattr(scr, "show_live"):
                _captured = reading
                def _get_eco2():
                    # Directly poll ENS160; update _captured in-place when new data arrives.
                    ens = getattr(air, '_ens', None)
                    if ens is not None:
                        try:
                            if ens.data_ready():
                                aqi, tvoc_ppb, eco2_ppm = ens.read_air_raw()
                                if eco2_ppm > 0:
                                    _captured.eco2_ppm = int(eco2_ppm)
                                    _captured.ready = True
                        except Exception:
                            pass
                    return _captured
                try:
                    a_live = scr.show_live(btn=btn, get_reading=_get_eco2, tick_fn=tick_fn)
                except TypeError:
                    a_live = scr.show_live(btn=btn)
                reset_and_flush(btn, flush_ms=min(180, flush_ms), poll_ms=poll_ms)
                if a_live in ("single", None):
                    _gc()
                    continue
                reset_and_flush(btn, flush_ms, poll_ms)
                return a_live

            # TVOC and other static screens use the captured reading
            else:
                scr.show(reading)

        except Exception as e:
            print("[FLOW] screen error:", name, repr(e))
            draw_text(oled, "ERR " + name.upper(), y=24)
            wait_for_single(btn, tick_fn=tick_fn)
            reset_and_flush(btn, flush_ms, poll_ms)
            return

        a = wait_for_single(btn, tick_fn=tick_fn)
        if a == "single" or a is None:
            reset_and_flush(btn, flush_ms=min(180, flush_ms), poll_ms=poll_ms)
            _gc()
            continue
        reset_and_flush(btn, flush_ms, poll_ms)
        return a

    # SUMMARY (after TEMP)
    _gc()   # (bug fix) reclaim heap before summary allocation
    _log_screen("summary")
    summ = get_screen("summary")
    if summ and hasattr(summ, "show_live"):
        try:
            # show_live polls btn and exits on any click
            summ.show_live(get_reading=lambda: reading, btn=btn, tick_fn=tick_fn)
        except Exception as e:
            print("[FLOW] summary error:", repr(e))
    else:
        draw_text(oled, "SUMMARY", y=24)
        wait_for_single(btn, tick_fn=tick_fn)

    _gc()   # (#2) reclaim all carousel transients before returning to waiting loop
    reset_and_flush(btn, flush_ms, poll_ms)



# ============================================================
# TIME FLOW (DOUBLE CLICK)
# ============================================================
def time_flow(btn, oled, cfg, wifi, ds3231, get_screen, flush_ms=250, poll_ms=25, status=None, tick_fn=None):
    # settle tail of triggering double click (prevents instant exit)
    _entry_settle(btn, poll_ms=poll_ms)

    ts = None

    # Prefer your screen registry if it supports it
    try:
        ts = get_screen("time")
    except Exception:
        ts = None

    # If registry didn't return a valid screen, construct it here (robust)
    if ts is None:
        try:
            from src.ui.screens.time import TimeScreen
            ts = TimeScreen(oled, cfg, wifi_manager=wifi, ds3231=ds3231, status=status)
        except Exception:
            ts = None

    if ts and hasattr(ts, "show_live"):
        try:
            try:
                _utc = ts._get_utc_tuple()
                _tz = (getattr(ts, "cfg", None) or {}).get("timezone_offset_min", None)
                if _utc:
                    _log_screen("time", "UTC={:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}  tz_offset={}min".format(
                        _utc[0], _utc[1], _utc[2], _utc[3], _utc[4], _utc[5], _tz))
                else:
                    _log_screen("time")
            except Exception:
                _log_screen("time")
            # hold forever until click
            ts.show_live(btn=btn, max_seconds=0, tick_fn=tick_fn)
            _post_screen_flush(btn, ms=120, poll_ms=poll_ms)
            return
        except Exception as e:
            print("[TIME] show_live error:", repr(e))

    # fallback
    draw_text(oled, "TIME", y=24)
    wait_for_single(btn, tick_fn=tick_fn)
    reset_and_flush(btn, flush_ms, poll_ms)


# ============================================================
# SLEEP / LOW POWER (LONG PRESS)
# ============================================================
def sleep_flow(btn, oled, get_screen, flush_ms=250, poll_ms=25, tick_fn=None):
    # Sleep is triggered by a 3 s hold-while-pressed, so we do NOT wait for
    # button release here. btn.reset() inside show_live captures the held state
    # cleanly: the eventual release is ignored, and the next fresh press is a click.
    _post_screen_flush(btn, ms=50, poll_ms=poll_ms)

    # Time screen shown first — sailpoint already lives in the single-click
    # carousel, and double-click now opens the nav state screen, so long
    # press is the Time screen's home. Single click advances to battery.
    time_scr = get_screen("time")
    try:
        if time_scr and hasattr(time_scr, "_get_utc_tuple"):
            _utc = time_scr._get_utc_tuple()
            _tz = (getattr(time_scr, "cfg", None) or {}).get("timezone_offset_min", None)
            if _utc:
                _log_screen("time", "UTC={:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}  tz={}min".format(
                    _utc[0], _utc[1], _utc[2], _utc[3], _utc[4], _utc[5], _tz))
            else:
                _log_screen("time")
        else:
            _log_screen("time")
    except Exception:
        _log_screen("time")
    if time_scr and hasattr(time_scr, "show_live"):
        try:
            a = time_scr.show_live(btn=btn, max_seconds=0, tick_fn=tick_fn)
        except Exception:
            a = None
    else:
        draw_text(oled, "Time", y=24)
        a = wait_for_single(btn, tick_fn=tick_fn)

    if a != "single":
        reset_and_flush(btn, flush_ms, poll_ms)
        return

    _post_screen_flush(btn, ms=120, poll_ms=poll_ms)

    # Battery screen; single click advances to sleep screen.
    bat_scr = get_screen("battery")
    try:
        if bat_scr and hasattr(bat_scr, "_read"):
            _bat = bat_scr._read()
            if _bat.get("present"):
                _log_screen("battery", "voltage={:.2f}V  current={:.0f}mA".format(
                    float(_bat.get("bus_v") or 0), float(_bat.get("current_ma") or 0)))
            else:
                _log_screen("battery", err="INA219 not connected")
        else:
            _log_screen("battery")
    except Exception:
        _log_screen("battery")
    if bat_scr and hasattr(bat_scr, "show_live"):
        try:
            a = bat_scr.show_live(btn)
        except Exception:
            a = None
    else:
        draw_text(oled, "Battery", y=24)
        a = wait_for_single(btn, tick_fn=tick_fn)

    if a != "single":
        reset_and_flush(btn, flush_ms, poll_ms)
        return

    _post_screen_flush(btn, ms=120, poll_ms=poll_ms)

    _log_screen("sleep")
    scr = get_screen("sleep")
    if scr and hasattr(scr, "show_live"):
        try:
            scr.show_live(btn, tick_fn=tick_fn)
        except Exception:
            pass
    else:
        draw_text(oled, "Low Power", y=24)
        wait_for_single(btn, tick_fn=tick_fn)

    reset_and_flush(btn, flush_ms, poll_ms)


# ============================================================
# SELF DESTRUCT (QUAD CLICK)
# ============================================================
def selfdestruct_flow(btn, oled, get_screen, flush_ms=250, poll_ms=25, tick_fn=None):
    _log_screen("selfdestruct")
    scr = get_screen("selfdestruct")

    if scr:
        # Preferred: show_live(btn)
        if hasattr(scr, "show_live"):
            try:
                scr.show_live(btn)
            except TypeError:
                try:
                    scr.show_live(btn=btn)
                except Exception:
                    pass
            except Exception:
                pass

        # Next: show(btn)
        elif hasattr(scr, "show"):
            try:
                scr.show(btn)
            except TypeError:
                try:
                    scr.show(btn=btn)
                except Exception:
                    pass
            except Exception:
                pass

        # Legacy: run()
        elif hasattr(scr, "run"):
            try:
                scr.run()
            except Exception:
                pass
    else:
        draw_text(oled, "SELFDESTRUCT", y=20)
        time.sleep_ms(800)

    reset_and_flush(btn, flush_ms, poll_ms)