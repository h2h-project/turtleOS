# main.py (device root) — AirBuddy 2.1 launcher
# Boot -> Waiting -> src/app/main.run()
#
# Board-agnostic version:
#  - Uses src.hal.board for I2C + GPS pins (Pico vs ESP32)
#  - OLED class now uses HAL by default (your patched src/ui/oled.py)
#
# Focused patch (ESP32 stability):
#  - Move WiFi earlier to avoid heap fragmentation (ESP32 WiFi PHY alloc crash)
#  - Delay heavy allocations (AirSensor) until AFTER WiFi attempt
#  - Add gc.collect() before WiFi init
#  - Shorten WiFi timeout + reduce retries for fast fail
#  - Hold each boot step on OLED for 0.5s (so errors are readable)
#  - If btn_pin missing in HAL, show message and stop instead of crashing

from machine import RTC
import time

from src.hal.board import init_i2c, gps_pins

# ----------------------------
# Boot pacing
# ----------------------------
BOOT_STEP_HOLD_MS = 500  # hold each step so OLED text is readable


# ----------------------------
# GPS pins (match src/app/main.py)
# ----------------------------
GPS_UART_ID, GPS_BAUD, GPS_TX_PIN, GPS_RX_PIN = gps_pins()


def _gc():
    try:
        import gc
        gc.collect()
    except Exception:
        pass


def _sleep_hold():
    try:
        time.sleep_ms(int(BOOT_STEP_HOLD_MS))
    except Exception:
        time.sleep(BOOT_STEP_HOLD_MS / 1000)


def _hex_list(addrs):
    try:
        return "[" + ", ".join("0x%02X" % a for a in addrs) + "]"
    except Exception:
        return "[]"


def i2c_scan():
    """
    One-shot I2C scan helper (safe).
    """
    try:
        i2c = init_i2c()
        return i2c.scan() or []
    except Exception:
        return []


# Common I2C addresses we care about
I2C_ADDR_OLED = 0x3C
I2C_ADDR_RTC = 0x68
I2C_ADDR_ENS160 = 0x53
I2C_ADDR_AHT2X = 0x38  # AHT10/AHT20/AHT21 often
I2C_ADDR_SCD41 = 0x62  # SCD40/SCD41 true CO2 sensor


# ----------------------------
# OLED init
# ----------------------------
def init_oled():
    try:
        from src.ui.oled import OLED

        # Read col_offset from config if available; default 0 (large/SSD1306).
        # Small 1.3" SH1106 modules typically need 2.
        col_offset = 0
        try:
            import json as _json
            with open("config.json", "r") as _f:
                _raw = _json.load(_f)
            col_offset = int(_raw.get("oled_col_offset", 0))
        except Exception:
            pass

        return OLED(col_offset=col_offset)

    except OSError as e:
        if e.args[0] == 19:  # ENODEV — no I2C device at that address
            print("[BOOT] OLED: not found — running headless")
        else:
            print("[BOOT] OLED: init failed:", repr(e))
            try:
                import sys
                sys.print_exception(e)
            except Exception:
                pass
        return None
    except Exception as e:
        print("[BOOT] OLED: init failed:", repr(e))
        try:
            import sys
            sys.print_exception(e)
        except Exception:
            pass
        return None


# ----------------------------
# Config
# ----------------------------
def load_cfg_dict():
    try:
        from config import load_config
        cfg = load_config()
        return cfg if isinstance(cfg, dict) else None
    except Exception as e:
        print("CONFIG error:", repr(e))
        return None


# ----------------------------
# RTC Sync (UTC)
# ----------------------------
def sync_rtc_from_ds3231():
    info = {"ok": False, "synced": False, "detected": False, "utc": True, "dt_utc": None, "temp_c": None}
    try:
        i2c = init_i2c()
        addrs = []
        try:
            addrs = i2c.scan() or []
        except Exception:
            pass

        if I2C_ADDR_RTC not in addrs:
            info["detected"] = False
            return False, "NOT DETECTED", info

        info["detected"] = True

        from src.app.rtc_sync import sync_system_rtc_from_ds3231
        out = sync_system_rtc_from_ds3231(i2c, tz_offset_s=0)  # DS3231 kept in UTC

        info["ok"] = bool(out.get("ok"))
        info["synced"] = bool(out.get("synced"))
        info["temp_c"] = out.get("temp_c")
        info["dt_utc"] = out.get("dt_utc")
        return bool(info["synced"]), ("OK" if info["synced"] else "ERROR"), info

    except Exception:
        return False, "ERROR", info


# ----------------------------
# API Device Lookup (LOW-RAM, inline)
# ----------------------------
def api_device_lookup(cfg):
    info = {
        "ok": False,
        "device_name": "",
        "home_name": "",
        "room_name": "",
        "time_zone": "",
        "tz_offset_min": None,
    }

    if not cfg:
        print("API lookup: no cfg")
        return False, "No config", info

    device_id = (cfg.get("device_id") or "").strip()
    device_key = (cfg.get("device_key") or "").strip()
    api_base = (cfg.get("api_base") or "").strip().rstrip("/")

    if not device_id or not device_key:
        print("API lookup: missing device keys")
        return False, "No device keys", info

    if not api_base:
        print("API lookup: missing api_base")
        return False, "No api_base", info

    # Accept either:
    #   http://air2.earthen.io
    #   http://air2.earthen.io/api
    if api_base.endswith("/api"):
        url = api_base + "/v1/device?compact=1"
    else:
        url = api_base + "/api/v1/device?compact=1"

    r = None

    try:
        _gc()

        try:
            import ujson as json
        except Exception:
            import json  # type: ignore

        import urequests

        headers = {
            "X-Device-Id": device_id,
            "X-Device-Key": device_key,
            "Accept": "application/json",
            "Connection": "close",
        }

        print("API lookup: GET", url)

        try:
            r = urequests.get(url, headers=headers, timeout=6)
        except TypeError:
            r = urequests.get(url, headers=headers)

        code = getattr(r, "status_code", None)
        print("API lookup: HTTP", code)

        if code != 200:
            return False, "HTTP {}".format(code if code is not None else "?"), info

        try:
            body = r.text
        except Exception as e:
            print("API lookup: body read error", repr(e))
            return False, "BODY READ FAIL", info

        print("API lookup: body bytes", len(body) if body else 0)

        if not body:
            return False, "Empty body", info

        try:
            data = json.loads(body)
        except Exception as e:
            print("API lookup: json parse error", repr(e))
            try:
                print("API lookup body:", body[:160])
            except Exception:
                pass
            return False, "Bad JSON", info

        if not isinstance(data, dict):
            return False, "API not dict", info

        if not data.get("ok"):
            return False, "API not ok", info

        dev = data.get("device") or {}
        asg = data.get("assignment") or {}
        home = asg.get("home") or {}
        room = asg.get("room") or {}
        user = asg.get("user") or {}

        info["device_name"] = str(
            dev.get("device_name")
            or data.get("device_name")
            or ""
        )

        info["home_name"] = str(
            home.get("home_name")
            or data.get("home_name")
            or ""
        )

        info["room_name"] = str(
            room.get("room_name")
            or data.get("room_name")
            or ""
        )

        info["time_zone"] = str(
            data.get("time_zone")
            or user.get("time_zone")
            or data.get("user_time_zone")
            or ""
        )

        try:
            tzm = data.get("tz_offset_min", data.get("timezone_offset_min", None))
            info["tz_offset_min"] = None if tzm is None else int(tzm)
        except Exception:
            info["tz_offset_min"] = None

        info["ok"] = True
        print("API lookup: success")
        return True, "device confirmed", info

    except MemoryError:
        print("API lookup: ENOMEM")
        return False, "ENOMEM", info

    except Exception as e:
        print("API error:", repr(e))
        return False, "API FAIL", info

    finally:
        if r:
            try:
                r.close()
            except Exception:
                pass
        _gc()
# ----------------------------
# GPS check (end of boot)
# ----------------------------
def gps_boot_check(cfg):
    info = {"ok": False, "enabled": False, "detected": False}

    try:
        enabled = bool(cfg and cfg.get("gps_enabled", False))
    except Exception:
        enabled = False

    info["enabled"] = enabled

    if not enabled:
        info["ok"] = True
        info["detected"] = False
        return True, "GPS off", info

    gps = None
    try:
        _gc()
        from src.app.gps_init import init_gps
        gps = init_gps(
            uart_id=GPS_UART_ID,
            baud=GPS_BAUD,
            tx_pin=GPS_TX_PIN,
            rx_pin=GPS_RX_PIN
        )
        if gps is None:
            info["ok"] = False
            info["detected"] = False
            return True, "NOT DETECTED", info

        # Presence-only check: just wait for any bytes on the UART — no read needed.
        # Max 5 seconds. Avoids uart.read(n) blocking on inter-character timeouts.
        start = time.ticks_ms()
        seen = False
        while time.ticks_diff(time.ticks_ms(), start) < 5000:
            try:
                if gps.uart.any():
                    seen = True
                    break
            except Exception:
                pass
            time.sleep_ms(100)

        info["detected"] = bool(seen)
        info["ok"] = bool(seen)
        return True, ("OK" if seen else "NOT DETECTED"), info

    except Exception:
        info["ok"] = False
        info["detected"] = False
        return True, "NOT DETECTED", info
    finally:
        gps = None
        _gc()


# ----------------------------
# Waiting Screen (root renders ONCE)
# ----------------------------
def go_waiting(oled, wifi_boot=None, api_boot=None, gps_boot=None):
    if oled is None:
        return
    try:
        from src.ui.waiting import WaitingScreen
        scr = WaitingScreen()
        scr.show(
            oled,
            line="Know thy air...",
            animate=False,
            wifi_ok=bool(isinstance(wifi_boot, dict) and wifi_boot.get("ok")),
            gps_on=bool(isinstance(gps_boot, dict) and gps_boot.get("enabled") and gps_boot.get("ok")),
            api_ok=bool(isinstance(api_boot, dict) and api_boot.get("ok")),
        )
    except Exception as e:
        print("WAITING error:", repr(e))


# ============================================================
# BOOT SEQUENCE
# ============================================================
try:
    import gc as _gc_mod
    _free = _gc_mod.mem_free()
    _total = _free + _gc_mod.mem_alloc()
    print("[BOOT] heap: {} KB free of {} KB total".format(_free // 1024, _total // 1024))
except Exception:
    pass

oled = init_oled()


def _preload_screens(oled):
    """
    Import screen module bytecodes while heap is still clean (pre-WiFi).
    Also pre-warm font metric paths on each writer.
    Both operations must complete before step_wifi() runs.

    Required on ESP32 AND Pico W when wifi_enabled=True.
    On ESP32: the WiFi PHY allocates ~16 KB of C-heap rx buffers; loading
    module bytecodes after that causes fragmentation-induced MemoryErrors.
    On Pico W: the CYW43 driver allocates Python-heap memory on connect;
    post-WiFi the heap is fragmented enough that even a 1280-byte bytecode
    (e.g. src.input.button) fails with MemoryError.
    Only skip when WiFi is disabled — then no PHY allocation occurs and the
    RAM cost of loading ~52 KB of bytecodes upfront is pure waste.
    """
    _gc()
    try:
        import gc as _gc_mod
        _f = _gc_mod.mem_free(); _t = _f + _gc_mod.mem_alloc()
        print("[PRELOAD] start: {} KB free of {} KB total".format(_f // 1024, _t // 1024))
    except Exception:
        pass

    # Pre-load bytecode for all commonly-used screens.
    # After WiFi fragments the heap these imports would fail with MemoryError.
    # With modules already in sys.modules, get_screen() only allocates the instance.
    # _gc() between each import helps coalesce free blocks on a tight heap.
    for mod in (
        # Load clicks before flows: flows imports clicks at top-level, so loading
        # clicks first lets gc() run between them rather than atomically.
        "src.ui.clicks",       # ~2 KB — used by flows + app.main
        "src.ui.flows",        # ~9 KB — carousel orchestration (top-level import in app.main)
        # app.main + button must be preloaded: they're imported post-WiFi when the
        # heap is fragmented, and their bytecode allocations (~7 KB, ~3 KB) fail there.
        "src.app.main",        # ~7 KB — the run() entry point
        "src.input.button",    # ~3 KB — imported at line 83 of run(); fails post-WiFi
        # Sensor modules: air.py is 887 lines / 30 KB source.  Importing it post-WiFi
        # fragments the heap enough that a 136-byte class-dict allocation fails.
        # Loading here (83 KB free, unfragmented) gives it plenty of room.
        "src.sensors.air",     # ~11 KB — AirSensor + AirReading + AHT21 + ENS160
        "src.drivers.scd4x",   # ~2 KB  — SCD41 driver (top-level import in air.py)
        # Sensor carousel screens: these are accessed on single-click (the most
        # common user action).  A failed WiFi connection attempt (12 s of association
        # retries) fragments the Python heap enough that even 1280-byte module-bytecode
        # allocations fail post-boot.  Loading them here, before WiFi runs, guarantees
        # they fit in the unfragmented heap.
        "src.ui.screens.co2",      # SCD41 CO2 live screen
        "src.ui.screens.tvoc",     # ENS160 TVOC screen
        "src.ui.screens.temp",     # AHT21/AHT10 temperature screen
        "src.ui.screens.temp2",    # SCD41 temperature screen
        "src.ui.screens.summary",  # summary screen (always shown at end of carousel)
        # eco2 is ENS160-only — load it too; it's small and accessed on every single-click
        # when an ENS160 is present.
        "src.ui.screens.eco2",     # ENS160 eCO2 screen
        # Connectivity screens: accessed on triple-click, which is rare.
        # Keep these lazy — triple-click is infrequent, and skipping them here saves
        # ~12 KB of bytecode during the preload window.
        # wifi, online, logging, device, time → lazy (get_screen retries on MemoryError)
    ):
        try:
            __import__(mod)
            _gc()  # coalesce after each import — reduces fragmentation
        except Exception:
            pass

    try:
        _f = _gc_mod.mem_free(); _t = _f + _gc_mod.mem_alloc()
        print("[PRELOAD] done:  {} KB free of {} KB total".format(_f // 1024, _t // 1024))
    except Exception:
        pass

    # Pre-warm font writers: triggers any lazy caches before WiFi runs.
    if oled:
        for attr in ("f_vsmall", "f_small", "f_med", "f_large",
                     "f_arvo", "f_arvo16", "f_arvo20"):
            try:
                w = getattr(oled, attr, None)
                if w:
                    w.size("A")
            except Exception:
                pass

    _gc()


# Preload screen modules before WiFi runs on any WiFi-capable platform.
# Both ESP32 and Pico W suffer post-WiFi heap fragmentation that prevents
# large module-bytecode allocations (see _preload_screens docstring).
#
# IMPORTANT: Only preload when WiFi will actually run.
# If wifi_enabled=False, the PHY never allocates, so preloading ~52 KB of
# bytecodes is pure cost — it exhausts the heap and starves AirSensor init.
# Load config early (before the usual boot step) so we can make this decision.
try:
    from src.hal.platform import platform_tag as _platform_tag
    _pt = _platform_tag()
    _is_esp32 = (_pt == "esp32")
    _is_pico  = (_pt == "pico")
    print("[BOOT] Board: {}".format(_pt))
except Exception:
    _pt = "unknown"
    _is_esp32 = False  # safe default: skip preload
    _is_pico  = False
    print("[BOOT] Board: unknown")

_preload_needed = False
if _is_esp32 or _is_pico:
    try:
        _early_cfg = load_cfg_dict()
        # Preload only if WiFi is enabled (or config unreadable — safe default)
        _preload_needed = (_early_cfg is None or bool(_early_cfg.get("wifi_enabled", False)))
    except Exception:
        _preload_needed = True  # safe default: preload when uncertain

# Pre-activate the WiFi PHY *before* preloading fragments the heap.
#
# ESP32: wlan.active(True) allocates ~16 KB of static rx buffers from C heap.
# After _preload_screens() those buffers can't fit contiguously → only 3 of 10
# succeed → "Expected to init 10 rx buffer, actual is 3" → broken driver state.
# Activating the radio here (C heap still unfragmented) gives WiFi all 10 buffers.
#
# Pico W / CYW43: creating WLAN(STA_IF) initialises the CYW43 driver and
# allocates its Python-heap working memory.  Doing this before preloading
# ensures those allocations land in the clean, unfragmented heap; after
# preloading, only the much-smaller per-connection state is allocated.
#
# wifi_manager.connect() is patched to skip active(False)→active(True) when
# the radio is already up, so this allocation is preserved through boot.
_wlan_pre = None
if (_is_esp32 or _is_pico) and _preload_needed:
    _net_pre = None
    try:
        import network as _net_pre
        _wlan_pre = _net_pre.WLAN(_net_pre.STA_IF)
        _wlan_pre.active(True)
        print("[BOOT] WiFi PHY pre-activated")
        # NOTE: do NOT call _wlan_pre.connect() here.
        # On ESP32: connecting early deadlocks step_wifi() via WiFi-task mutex.
        # On Pico:  connecting early is safe but unnecessary — step_wifi() will
        # connect cleanly from IDLE state once the radio is already active.
    except Exception as _pre_e:
        print("[BOOT] WiFi PHY pre-activate failed:", repr(_pre_e))
        _wlan_pre = None
        # Pre-activation failed — skip preload (its only purpose is to protect
        # against post-WiFi heap fragmentation; without WiFi there is no benefit).
        _preload_needed = False
        # ESP32 only: kill the half-started C WiFi task to stop it spamming
        # "esp_netif_new_api / nvs alloc out of memory" errors after boot.
        # Pico W: never call deinit() — it permanently destroys the CYW43 driver
        # and prevents any reconnection attempt later in the session.
        if _is_esp32 and _net_pre is not None:
            try:
                _net_pre.deinit()
            except Exception:
                pass
    # Drop the Python reference — the C/CYW43 radio stays active (idle).
    _wlan_pre = None
    _net_pre = None

if _preload_needed:
    _preload_screens(oled)
else:
    _gc()  # just collect — lazy imports work fine without WiFi PHY

cfg = None
rtc_info = None
wifi_boot = None
api_boot = None
gps_boot = None

# IMPORTANT: delay AirSensor creation until AFTER WiFi attempt on ESP32
air = None

try:
    from src.ui.booter import Booter
    booter = Booter(oled) if oled else None
except Exception:
    booter = None


def _log(msg):
    print(msg)


def step_load_config():
    global cfg
    cfg = load_cfg_dict()
    return (cfg is not None), ("Config OK" if cfg else "Config FAIL")


def step_wifi():
    """
    ESP32-safe WiFi boot:
      - gc.collect() before WiFi init to reduce fragmentation
      - increased timeout + 1 retry to survive slow DHCP / congested APs

    IMPORTANT:
      - Keep WiFi alive if connect succeeds, because the next boot step
        (step_api) immediately needs network access.
      - Only tear WiFi down on failure.
    """
    global wifi_boot
    wifi_boot = {"ok": False, "supported": False}

    if not cfg:
        return True, "SKIPPED (No config)"

    if not cfg.get("wifi_enabled", False):
        wifi_boot = {"ok": False, "supported": False}
        try:
            from src.ui import connection_header
            connection_header.set_wifi_enabled(False)
        except Exception:
            pass
        return True, "DISABLED"

    try:
        from src.net.net_caps import wifi_supported
        supported = bool(wifi_supported())
    except Exception:
        supported = False

    wifi_boot["supported"] = supported

    if not supported:
        return True, "NOT SUPPORTED"

    wifi = None
    ok = False
    try:
        _gc()
        from src.net.wifi_manager import WiFiManager
        wifi = WiFiManager()

        try:
            print("[WIFI] active={}".format(wifi.enabled()))
        except Exception:
            pass
        try:
            print("[WIFI] connected={}".format(wifi.is_connected()))
        except Exception:
            pass
        try:
            print("[WIFI] status={}".format(wifi.status_code()))
        except Exception:
            pass

        # Dot ticker: updates OLED footer with "WiFi connect...." on each
        # connection poll (once per second on ESP32).
        # WiFi is step 1 of 6, so p_prev = 1/6 ≈ 0.167.
        _dots = [0]

        def _wifi_tick():
            _dots[0] += 1
            if booter:
                try:
                    booter._draw_frame(p=1.0 / 6.0, footer="WiFi connect" + "." * _dots[0])
                except Exception:
                    pass

        ok, ip, status = wifi.connect(
            cfg.get("wifi_ssid", ""),
            cfg.get("wifi_password", ""),
            # ESP32/ESP32-S3: RTCWDT fires at ~15s — stay under that threshold.
            # Pico W / CYW43: no RTCWDT constraint; allow one retry because the
            # CYW43 driver often needs a second attempt on first cold-boot
            # (first attempt times out at 12s; retry succeeds cleanly).
            timeout_s=12,
            retry=1 if _is_pico else 0,
            tick_cb=_wifi_tick
        )

        if "WPA3" in status:
            detail = "WPA3 FAIL"
        elif ok:
            detail = "OK"
        else:
            detail = "FAIL"

        wifi_boot = {"ok": bool(ok), "supported": True, "ip": ip, "status": status}
        return True, detail

    except Exception as e:
        wifi_boot = {"ok": False, "supported": True, "error": repr(e)}
        return True, "ERROR"

    finally:
        # Keep WiFi up if connection succeeded, because step_api runs next.
        if not ok:
            if wifi is not None:
                try:
                    wifi.active(False)
                except Exception:
                    pass
            # Only deinit on ESP32 — on Pico, network.deinit() kills the CYW43
            # driver permanently and prevents reconnection from the main loop or
            # WiFi screen later in the session.
            if _is_esp32:
                try:
                    import network
                    if hasattr(network, "deinit"):
                        network.deinit()
                except Exception:
                    pass

        wifi = None
        _gc()
        _gc()


def step_api():
    global api_boot
    if not (isinstance(wifi_boot, dict) and wifi_boot.get("supported") and wifi_boot.get("ok")):
        api_boot = {"ok": False}
        try:
            from src.ui import connection_header
            connection_header.set_api_ok(False)
        except Exception:
            pass
        return True, "SKIPPED (No WiFi)"

    ok, detail, info = api_device_lookup(cfg)
    api_boot = info

    try:
        from src.ui import connection_header
        connection_header.set_api_ok(bool(ok))
    except Exception:
        pass

    return True, ("OK" if ok else detail)


def step_rtc():
    global rtc_info
    ok, detail, info = sync_rtc_from_ds3231()
    rtc_info = info

    if not info.get("detected"):
        return True, "NOT DETECTED"

    if info.get("synced"):
        dt = info.get("dt_utc")
        if dt and len(dt) >= 7:
            y, mo, d, _wd, h, mi, s = dt[0], dt[1], dt[2], dt[3], dt[4], dt[5], dt[6]
            utc_str = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d} UTC".format(y, mo, d, h, mi, s)

            # Prefer tz_offset_min from API response, fall back to config
            tz_min = None
            try:
                if isinstance(api_boot, dict):
                    tz_min = api_boot.get("tz_offset_min")
            except Exception:
                pass
            if tz_min is None and cfg:
                try:
                    tz_min = cfg.get("timezone_offset_min")
                except Exception:
                    pass

            if tz_min is not None:
                try:
                    tz_min = int(tz_min)
                    total_min = h * 60 + mi + tz_min
                    lh = (total_min // 60) % 24
                    lmi = total_min % 60
                    tz_sign = "+" if tz_min >= 0 else "-"
                    tz_abs = abs(tz_min)
                    tz_hh = tz_abs // 60
                    tz_mm = tz_abs % 60
                    tz_label = "UTC{}{}".format(tz_sign, tz_hh) if tz_mm == 0 else "UTC{}{}:{:02d}".format(tz_sign, tz_hh, tz_mm)
                    print("[BOOT] RTC time: {} / {:02d}:{:02d}:{:02d} local ({})".format(
                        utc_str, lh, lmi, s, tz_label))
                except Exception:
                    print("[BOOT] RTC time:", utc_str)
            else:
                print("[BOOT] RTC time:", utc_str)

        return True, "OK"

    return True, "SYNC FAIL"


def step_warmup():
    """
    Only warm up if ENS160/AHT appears on I2C.

    NOTE: We lazily construct AirSensor here to avoid heap fragmentation
    before WiFi init on ESP32.
    """
    global air

    addrs = i2c_scan()
    has_air = (I2C_ADDR_ENS160 in addrs) or (I2C_ADDR_AHT2X in addrs)
    has_scd41 = (I2C_ADDR_SCD41 in addrs)

    print("[BOOT] SCD41: {}".format("FOUND (0x62)" if has_scd41 else "NOT FOUND"))

    if not has_air and not has_scd41:
        return True, "NO SENSORS DETECTED"

    if air is None:
        try:
            _gc()
            _gc()  # extra pass: reclaim anything WiFi left behind
            from src.sensors.air import AirSensor
            # Use 100 kHz for the sensor I2C bus — SCD4X is rated for max 100 kHz.
            # AHT10 and ENS160 both tolerate 100 kHz; the OLED/DS3231 also work
            # at this speed (they were at 400 kHz but 100 kHz is fine).
            air = AirSensor(freq=100_000)
        except Exception as e:
            print("AIR init failed:", repr(e))
            air = None
            return True, "AIR INIT FAIL"

    # Initialize hardware NOW so the SCD4x starts its periodic measurement
    # cycle during the warmup window (not lazily on first button press).
    # Without this, _ensure_hw() is deferred until finish_sampling() fires,
    # and the SCD41 never has its ~5 s measurement cycle running at boot.
    try:
        air._ensure_hw()
    except Exception as e:
        print("AIR hw init failed:", repr(e))

    try:
        warmup_s = float(cfg.get("warmup_seconds", 4.0) if cfg else 4.0)
    except Exception:
        warmup_s = 4.0

    try:
        air.begin_sampling(warmup_seconds=warmup_s, source="boot")
        return True, "Warmup {:.0f}s".format(warmup_s)
    except Exception:
        return True, "WARMUP ERROR"  # non-fatal


def step_gps():
    global gps_boot
    ok, detail, info = gps_boot_check(cfg)
    gps_boot = info
    return True, detail


# Reordered: WiFi earlier (ESP32 heap stability) + API right after WiFi
steps = [
    ("Loading config...", step_load_config),
    ("WiFi connect", step_wifi),
    ("Device API check...", step_api),
    ("RTC clock...", step_rtc),
    ("Warming sensors...", step_warmup),
    ("GPS check...", step_gps),
]

if booter:
    try:
        booter.boot_pipeline(
            steps,
            intro_ms=500,
            fps=18,
            settle_ms=BOOT_STEP_HOLD_MS,  # <-- hold each step on OLED
            logger=_log
        )
    except Exception as e:
        print("BOOTER error:", repr(e))
else:
    for label, fn in steps:
        _log("[BOOT] " + label)
        try:
            ok, detail = fn()
            _log("[BOOT] {} -> {}".format(label, detail))
        except Exception as e:
            _log("[BOOT] {} -> ERROR {}".format(label, repr(e)))
        _sleep_hold()  # <-- hold each step even without OLED


# Show waiting immediately after boot (single render)
go_waiting(oled, wifi_boot=wifi_boot, api_boot=api_boot, gps_boot=gps_boot)


# ------------------------------------------------------------
# Preflight: Button HAL must exist (avoid crash loop)
# ------------------------------------------------------------
_btn_hal_ok = True
try:
    from src.hal.board import btn_pin  # noqa: F401
except Exception as e:
    _btn_hal_ok = False
    msg = "HAL missing btn_pin()"
    print(msg, repr(e))
    # Try show on OLED and stop so you can fix HAL without reboot loop
    try:
        if oled:
            from src.ui.waiting import WaitingScreen
            WaitingScreen().show(oled, line=msg, animate=False, wifi_ok=False, gps_on=False, api_ok=False)
            _sleep_hold()
    except Exception:
        pass


# Launch app loop
if _btn_hal_ok:
    try:
        from src.app.main import run
        run(
            rtc_synced=bool(rtc_info and rtc_info.get("synced")),
            wifi_boot=wifi_boot,
            api_boot=api_boot,
            oled=oled,
            air_sensor=air,
            boot_warmup_started=True,
            rtc_info=rtc_info
        )
    except Exception as e:
        print("AirBuddy boot error:", repr(e))
        raise
else:
    # HAL is broken — show message for 30 s then auto-reset so a redeploy takes effect
    import machine as _machine
    _deadline = time.ticks_add(time.ticks_ms(), 30_000)
    while time.ticks_diff(_deadline, time.ticks_ms()) > 0:
        time.sleep(5)
    _machine.reset()