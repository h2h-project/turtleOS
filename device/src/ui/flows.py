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


def _fetch_device_info(cfg):
    """
    Low-mem GET to fetch device info for DeviceScreen.
    Normalizes your server JSON into flat keys:
      device_name, home_name, room_name, community_name
    Returns dict (possibly empty).
    """
    if not isinstance(cfg, dict):
        return {}

    api_base = (cfg.get("api_base") or "").strip().rstrip("/")
    device_id = (cfg.get("device_id") or "").strip()
    device_key = (cfg.get("device_key") or "").strip()

    if (not api_base) or (not device_id) or (not device_key):
        return {}

    try:
        import urequests as requests
    except Exception:
        return {}

    headers = {
        "X-Device-Id": device_id,
        "X-Device-Key": device_key,
    }

    # Your server route is /api/v1/device
    urls = (
        api_base + "/api/v1/device?compact=1",
        api_base + "/api/v1/device",
        # safety fallbacks if api_base already includes /api
        api_base + "/v1/device?compact=1",
        api_base + "/v1/device",
    )

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

    for url in urls:
        r = None
        try:
            _gc()
            r = requests.get(url, headers=headers)
            code = getattr(r, "status_code", None)

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
                return {}

            try:
                data = j.loads(txt)
            except Exception:
                return {}

            out = _normalize(data)
            return out if out else (data if isinstance(data, dict) else {})

        except Exception:
            try:
                if r:
                    r.close()
            except Exception:
                pass
            _gc()
            continue

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
    # 1) WIFI SCREEN — skip with frowny if WiFi is not enabled in config
    # ------------------------------------------------------------
    _wifi_cfg_on = bool(cfg.get("wifi_enabled", False)) if isinstance(cfg, dict) else False
    if not _wifi_cfg_on:
        _show_frowny(oled, btn, "WiFi not enabled", "on your device")
        return _exit(None)

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
    online_scr = get_screen("online")
    if online_scr and hasattr(online_scr, "show_live"):
        try:
            a = online_scr.show_live(btn, tick_fn=tick_fn)
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
    # 3) LOGGING SCREEN
    # ------------------------------------------------------------
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
    # 4) GPS SCREEN
    # ------------------------------------------------------------
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

    if a != "single":
        return _exit(a)

    _post_screen_flush(btn, ms=120, poll_ms=poll_ms)

    # ------------------------------------------------------------
    # 5) DEVICE SCREEN
    # ------------------------------------------------------------
    device_scr = get_screen("device")
    if device_scr and hasattr(device_scr, "show_live"):
        try:
            api_info = _fetch_device_info(cfg)
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
):
    if air is None:
        _show_frowny(oled, btn, "Ack! No sensors", "are connected!")
        return

    # One full reading for CO2/TVOC screens
    try:
        reading = air.finish_sampling(log=False)
    except Exception as e:
        print("[FLOW] finish_sampling error:", repr(e))
        return

    gc_collect()
    # Preload ALL carousel screens now, while the heap is clean.
    # If _bg_tick fires telemetry during a dwell, get_screen() will return
    # the cached instance without needing a 1280-byte module bytecode allocation.
    for _n in ("co2", "tvoc", "temp", "summary"):
        get_screen(_n)
        _gc()
    reset_and_flush(btn, flush_ms, poll_ms)

    for name in ("co2", "tvoc", "temp"):
        _gc()
        scr = get_screen(name)
        if not scr:
            print("[FLOW] screen missing:", name)
            continue

        try:
            # TEMP: let the temp screen run its own live loop, then continue to SUMMARY
            if name == "temp" and hasattr(scr, "show_live"):
                try:
                    # Preferred: temp screen can pull fresh samples from `air`
                    scr.show_live(btn=btn, air=air, tick_fn=tick_fn)
                except TypeError:
                    # Fallback: temp screen only wants btn
                    scr.show_live(btn=btn)

                # (#3) Use the last good reading already captured by TempScreen's live
                # loop — avoids a second finish_sampling() allocation right before summary.
                if getattr(air, '_last', None) is not None:
                    reading = air._last

                # Prevent the click that exited temp from instantly skipping summary
                reset_and_flush(btn, flush_ms=min(180, flush_ms), poll_ms=poll_ms)
                break  # exit loop → go show summary

            # CO2/TVOC screens use the captured reading
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
            # hold forever until click
            ts.show_live(btn=btn, max_seconds=0, tick_fn=tick_fn)
            _post_screen_flush(btn, ms=120, poll_ms=poll_ms)
            return
        except Exception:
            pass

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