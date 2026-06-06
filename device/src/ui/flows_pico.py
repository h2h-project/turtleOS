# src/ui/flows_pico.py — Pico-trimmed screen flow logic
# Deployed to Pico as src/ui/flows.py by airbuddy_pico_sync.sh.
#
# Removed vs full flows.py:
#   - _json() and _fetch_device_info() — no in-carousel HTTP on Pico (heap too low)
#   - _offline_notice() — dead code (connectivity carousel never calls it)
#   - connectivity_carousel step 5 (GPS screen) — gps.py excluded from Pico
#   - sensor_carousel turtle_mode block (sailpoint/servo/gps/destination screens)
#   - sleep_flow sailpoint first-step — sailpoint.py excluded from Pico

import time
from src.ui.clicks import (
    draw_text,
    wait_for_single,
    wait_release,
    dwell_or_click,
    reset_and_flush,
    gc_collect,
)


def _gc():
    try:
        import gc
        gc.collect()
    except Exception:
        pass


def _post_screen_flush(btn, ms=90, poll_ms=25):
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
    try:
        wait_release(btn)
    except Exception:
        pass
    _post_screen_flush(btn, ms=140, poll_ms=poll_ms)


def _draw_center_lines(oled, lines, y0=18, line_h=12):
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


def _show_frowny(oled, btn, line1, line2):
    try:
        from src.ui.screens.frowny import FrownyScreen
        FrownyScreen(oled).show(btn, line1=line1, line2=line2)
    except Exception:
        _draw_center_lines(oled, [line1, line2], y0=22, line_h=12)
        try:
            time.sleep_ms(2000)
        except Exception:
            pass


# ============================================================
# CONNECTIVITY CAROUSEL (TRIPLE CLICK)
# ============================================================
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
    Triple-click flow (Pico carousel order):
      WiFi → Online → Device → Logging → (done)
    GPS screen removed — gps.py is excluded from the Pico build.
    """
    _entry_settle(btn, poll_ms=poll_ms)

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
    # 1) WIFI SCREEN
    # ------------------------------------------------------------
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
    if a != "single":
        return _exit(a)

    try:
        wifi_ok = bool(status.get("wifi_ok"))
    except Exception:
        wifi_ok = False

    if not wifi_ok:
        return _exit(None)

    _post_screen_flush(btn, ms=120, poll_ms=poll_ms)

    # ------------------------------------------------------------
    # 2) ONLINE/API SCREEN
    # ------------------------------------------------------------
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
    # 3) DEVICE SCREEN — boot-time cache only; no in-carousel HTTP on Pico
    # ------------------------------------------------------------
    device_scr = get_screen("device")
    if device_scr and hasattr(device_scr, "show_live"):
        try:
            api_info = device_info if (
                isinstance(device_info, dict)
                and device_info.get("ok")
                and device_info.get("device_name")
            ) else {}
            if api_info:
                print("[DEVICE] using boot cache:", api_info.get("device_name"))
                try:
                    from src.ui import connection_header as _ch_dev
                    _ch_dev.set_api_ok(True)
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

    try:
        reading = air.finish_sampling(log=False)
    except Exception as e:
        print("[FLOW] finish_sampling error:", repr(e))
        return

    gc_collect()

    _has_scd41 = getattr(air, '_scd41', None) is not None
    _has_ens   = getattr(air, '_ens',   None) is not None
    _has_aht   = getattr(air, '_aht',   None) is not None
    _has_aht10 = getattr(air, '_aht10', None) is not None
    _has_bme   = getattr(air, '_bme',   None) is not None

    _sensor_screens = []
    if _has_scd41:
        _sensor_screens.append("co2")
    if _has_ens:
        _sensor_screens.append("eco2")
        _sensor_screens.append("tvoc")
    if _has_aht or _has_bme:
        _sensor_screens.append("temp")
    if _has_scd41:
        _sensor_screens.append("temp2")

    print("[SINGLE] screens:", _sensor_screens if _sensor_screens else "none")

    # Preload all carousel screens now, while the heap is relatively clean.
    for _n in _sensor_screens:
        get_screen(_n)
        _gc()
    reset_and_flush(btn, flush_ms, poll_ms)

    for name in _sensor_screens:
        _gc()
        scr = get_screen(name)
        if not scr:
            print("[FLOW] screen missing:", name)
            continue

        try:
            if name in ("temp", "temp2") and hasattr(scr, "show_live"):
                try:
                    _a_temp = scr.show_live(btn=btn, air=air, tick_fn=tick_fn)
                except TypeError:
                    _a_temp = scr.show_live(btn=btn)
                if _a_temp == "sleep":
                    reset_and_flush(btn, flush_ms, poll_ms)
                    return _a_temp

                if getattr(air, '_last', None) is not None:
                    reading = air._last

                _remaining = _sensor_screens[_sensor_screens.index(name) + 1:]
                _next_is_temp = bool(_remaining and _remaining[0] in ("temp", "temp2"))
                reset_and_flush(btn, flush_ms=min(180, flush_ms), poll_ms=poll_ms)
                if _next_is_temp:
                    continue
                break

            elif name == "co2" and hasattr(scr, "show_live"):
                _captured = reading
                def _get_co2():
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

            elif name == "eco2" and hasattr(scr, "show_live"):
                _captured = reading
                def _get_eco2():
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

    _gc()
    reset_and_flush(btn, flush_ms, poll_ms)


# ============================================================
# TIME FLOW (DOUBLE CLICK)
# ============================================================
def time_flow(btn, oled, cfg, wifi, ds3231, get_screen, flush_ms=250, poll_ms=25, status=None, tick_fn=None):
    _entry_settle(btn, poll_ms=poll_ms)

    ts = None
    try:
        ts = get_screen("time")
    except Exception:
        ts = None

    if ts is None:
        try:
            from src.ui.screens.time import TimeScreen
            ts = TimeScreen(oled, cfg, wifi_manager=wifi, ds3231=ds3231, status=status)
        except Exception:
            ts = None

    if ts and hasattr(ts, "show_live"):
        try:
            ts.show_live(btn=btn, max_seconds=0, tick_fn=tick_fn)
            _post_screen_flush(btn, ms=120, poll_ms=poll_ms)
            return
        except Exception as e:
            print("[TIME] show_live error:", repr(e))

    draw_text(oled, "TIME", y=24)
    wait_for_single(btn, tick_fn=tick_fn)
    reset_and_flush(btn, flush_ms, poll_ms)


# ============================================================
# SLEEP / LOW POWER (LONG PRESS)
# ============================================================
def sleep_flow(btn, oled, get_screen, flush_ms=250, poll_ms=25, tick_fn=None):
    # Sailpoint step removed on Pico — sailpoint.py excluded from Pico build.
    # Goes directly to battery → sleep.
    _post_screen_flush(btn, ms=50, poll_ms=poll_ms)

    bat_scr = get_screen("battery")
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
        elif hasattr(scr, "run"):
            try:
                scr.run()
            except Exception:
                pass
    else:
        draw_text(oled, "SELFDESTRUCT", y=20)
        time.sleep_ms(800)

    reset_and_flush(btn, flush_ms, poll_ms)
