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
I2C_ADDR_OLED    = 0x3C
I2C_ADDR_RTC     = 0x68
I2C_ADDR_ENS160  = 0x53
I2C_ADDR_AHT2X   = 0x38  # AHT10/AHT20/AHT21
I2C_ADDR_SCD41   = 0x62  # SCD40/SCD41 true CO2 sensor
I2C_ADDR_BME280     = 0x76  # BME280 temp + humidity + pressure (SDO=LOW)
I2C_ADDR_BME280_ALT = 0x77  # BME280 alternate address (SDO=HIGH)
I2C_ADDR_QMC5883 = 0x0D  # QMC5883L (GY-271 clone)
I2C_ADDR_HMC5883 = 0x1E  # HMC5883L (genuine)
I2C_ADDR_AS5600  = 0x36  # AS5600 magnetic angle sensor (sail position)

_I2C_NAMES = {
    0x0D: "QMC5883L",
    0x1E: "HMC5883L",
    0x36: "AS5600",
    0x38: "AHT21",
    0x3C: "OLED",
    0x40: "INA219",
    0x53: "ENS160",
    0x62: "SCD41",
    0x68: "DS3231",
    0x76: "BME280",
    0x77: "BME280",
}


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
        if e.args[0] in (5, 19, 110):  # EIO, ENODEV, ETIMEDOUT — no I2C device present
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
    info = {"ok": False, "synced": False, "detected": False, "utc": True,
            "dt_utc": None, "temp_c": None, "battery_ok": None}
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

        # Check Oscillator Stop Flag (bit 7, status register 0x0F).
        # OSF=1 means DS3231 lost power since it was last set (dead/missing battery).
        try:
            osf = bool(i2c.readfrom_mem(0x68, 0x0F, 1)[0] & 0x80)
            info["battery_ok"] = not osf
            if osf:
                print("[RTC] WARNING: OSF set — DS3231 lost power (check battery)")
        except Exception:
            pass

        try:
            from src.app.rtc_sync import sync_system_rtc_from_ds3231
            out = sync_system_rtc_from_ds3231(i2c, tz_offset_s=0)
            info["ok"] = bool(out.get("ok"))
            info["synced"] = bool(out.get("synced"))
            info["temp_c"] = out.get("temp_c")
            info["dt_utc"] = out.get("dt_utc")
            info["reason"] = out.get("reason")
            info["error"] = out.get("error")

        except MemoryError:
            # Module import OOM at boot (fragmented heap) — fall back to inline sync.
            # RTC is already imported at module level so no new imports needed.
            print("[RTC] OOM importing rtc_sync — using inline sync")
            try:
                data = i2c.readfrom_mem(0x68, 0x00, 7)
                def _bcd(b): return ((b >> 4) & 0xF) * 10 + (b & 0xF)
                sec = _bcd(data[0] & 0x7F)
                mi  = _bcd(data[1] & 0x7F)
                hr  = _bcd(data[2] & 0x3F)
                wd  = int(data[3] & 0x07)   # 1..7, DS3231 convention
                d   = _bcd(data[4] & 0x3F)
                mo  = _bcd(data[5] & 0x1F)
                y   = _bcd(data[6]) + 2000
                if y >= 2020 and 1 <= mo <= 12 and 1 <= d <= 31:
                    wd0 = max(0, (wd - 1) % 7)  # 0=Mon for machine.RTC
                    RTC().datetime((y, mo, d, wd0, hr, mi, sec, 0))
                    info["synced"] = True
                    info["ok"] = True
                    info["dt_utc"] = (y, mo, d, wd0, hr, mi, sec)
                    print("[RTC] inline sync OK: {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
                        y, mo, d, hr, mi, sec))
                else:
                    info["reason"] = "year_below_min"
                    print("[RTC] inline: bad date y={} m={} d={}".format(y, mo, d))
            except Exception as _ie:
                info["reason"] = "inline_fail"
                print("[RTC] inline sync error:", repr(_ie))

        if not info["synced"]:
            _r = info.get("reason") or info.get("error") or "?"
            _dt = info.get("dt_utc")
            print("[RTC] sync fail: reason={} dt={}".format(_r, _dt))

        return bool(info["synced"]), ("OK" if info["synced"] else "ERROR"), info

    except Exception as e:
        print("[RTC] unexpected error:", repr(e))
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
        _dname = info.get("device_name") or "device"
        print("API lookup: {} is connected!".format(_dname))
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
        try:
            from src.sensors.ublox6gps import Ublox6GPS
            gps = Ublox6GPS(uart_id=GPS_UART_ID, baud=GPS_BAUD, tx_pin=GPS_TX_PIN, rx_pin=GPS_RX_PIN)
        except Exception as e:
            print("GPS:init skipped:", repr(e))
            gps = None
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
    # In turtle_mode the main loop immediately shows the turtle animation, so
    # skip the logo and clear the screen to avoid a 1-second logo flash.
    try:
        _tw_cfg = _early_cfg or {}
        if _tw_cfg.get("turtle_mode", False):
            oled.oled.fill(0)
            oled.oled.show()
            return
    except Exception:
        pass
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

try:
    _tq = 0
    try:
        with open("telemetry_queue.json", "r") as _tqf:
            for _tql in _tqf:
                if _tql.strip():
                    _tq += 1
    except OSError:
        pass  # file absent = 0 pending readings
    print("[BOOT] telemetry queue: {} reading{} pending".format(_tq, "s" if _tq != 1 else ""))
    del _tq
except Exception:
    pass

try:
    from src.hal.platform import platform_tag as _platform_tag
    _pt = _platform_tag()
    _is_esp32 = _pt in ("esp32", "esp32s3", "xiao_esp32s3")
    _is_pico  = (_pt == "pico")
    try:
        import json as _ptj
        with open("config.json") as _ptf:
            _pt_cfg_tag = str(_ptj.load(_ptf).get("board_type", "") or "").strip().lower()
        _pt_source = "config" if _pt_cfg_tag == _pt else "detected"
    except Exception:
        _pt_source = "detected"
    print("[BOOT] Board: {} ({})".format(_pt, _pt_source))
    _hal_module = {
        "pico":          "board_pico",
        "esp32":         "board_esp32",
        "esp32s3":       "board_esp32_s3",
        "xiao_esp32s3":  "board_xiao_esp32_s3",
    }.get(_pt, "board_esp32_s3 (fallback)")
    print("[BOOT] Using HAL: {}".format(_hal_module))
except Exception:
    _pt = "unknown"
    _is_esp32 = False  # safe default: skip preload
    _is_pico  = False
    print("[BOOT] Board: unknown")

# Pico W: import the LARGEST modules before OLED+fonts allocate 53 KB of
# fragmented heap.  Each .py import needs a contiguous read buffer = file size.
# air.py (36 KB) and src.app.main (23 KB, pulls flows+clicks as side effects)
# cannot fit after OLED fragments the heap; they can fit on a clean heap here.
# If either import fails, the preload list below retries as a fallback.
if _is_pico:
    import gc as _early_gc
    _early_log = []
    _early_log.append("start {} KB free".format(_early_gc.mem_free() // 1024))
    # Import large modules BEFORE OLED+fonts fragments the heap.
    # Each .py file needs a contiguous read buffer = source file size.
    # Order matters: largest first while heap is cleanest.
    #   air.py                    ~36 KB  ← biggest, must be first
    #   app/main.py               ~23 KB  ← pulls flows+clicks as side-effects
    #   glyphs.py                 ~28 KB  ← imported by connection_header → waiting → oled.__init__
    #   connection_header          ~6 KB  ← imported by waiting → oled.__init__ (imports glyphs)
    #   thermobar.py               ~8.5 KB ← needed by eco2/tvoc sensor screens
    #   sensor screens             ~10 KB each ← needed for single-click carousel; heap too
    #                                           fragmented at runtime to import lazily (~13 KB free)
    #   rtc_sync.py                ~9 KB  ← used inside run()
    #   telemetry_scheduler.py    ~23 KB  ← lazily imported by TelemetryState._ensure_scheduler();
    #                                        fails with MemoryError on fragmented post-WiFi heap,
    #                                        silently breaking all telemetry. Must load here.
    for _em in ("src.sensors.air", "src.app.main", "src.ui.glyphs",
                "src.ui.connection_header", "src.ui.thermobar",
                "src.ui.screens.eco2", "src.ui.screens.tvoc",
                "src.ui.screens.temp",
                "src.ui.screens.selfdestruct",
                "src.app.rtc_sync",
                "src.app.telemetry_scheduler"):
        try:
            __import__(_em)
            _early_gc.collect()
            _early_log.append("OK {} {} KB free".format(_em.split(".")[-1], _early_gc.mem_free() // 1024))
        except Exception as _em_e:
            _early_gc.collect()
            _early_log.append("FAIL {} {}".format(_em.split(".")[-1], repr(_em_e)))
    try:
        with open("early_diag.txt", "w") as _edf:
            _edf.write("\n".join(_early_log) + "\n")
    except Exception:
        pass
    del _early_gc, _em, _early_log

# I2C diagnostic: confirm which pins are in use and scan the bus before
# attempting OLED init, so a wiring problem is distinguishable from a driver problem.
try:
    from src.hal.board import i2c_pins as _i2c_pins_fn
    _di2c_id, _di2c_scl, _di2c_sda, _di2c_freq = _i2c_pins_fn()
    print("[BOOT] I2C: I2C({}) SDA=GPIO{} SCL=GPIO{} {}kHz".format(
        _di2c_id, _di2c_sda, _di2c_scl, _di2c_freq // 1000))
    _di2c = init_i2c()
    _di2c_found = _di2c.scan() or []
    if _di2c_found:
        _di2c_labeled = [
            "{} {}".format(hex(a), _I2C_NAMES[a]) if a in _I2C_NAMES else hex(a)
            for a in _di2c_found
        ]
        print("[BOOT] I2C scan: [{}]".format(", ".join(_di2c_labeled)))
    else:
        print("[BOOT] I2C scan: no devices found — check wiring/pull-ups")
    del _di2c, _di2c_found
except Exception as _di2c_e:
    print("[BOOT] I2C scan: FAILED", repr(_di2c_e))

oled = init_oled()

# Boot guard: hold button 2 s at power-on, OR create "debug_mode" file on flash.
# Either triggers a clean halt → MicroPython REPL (for sensor/hardware testing).
try:
    from src.app.boot_guard import check as _boot_guard_check
    if _boot_guard_check(oled):
        raise SystemExit
except SystemExit:
    raise
except Exception as _bg_e:
    print("[BOOT] boot_guard error (ignored):", repr(_bg_e))


def _preload_screens(oled, is_pico=False, turtle_mode=False):
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

    Pico W has only ~70 KB free Python heap (CYW43 firmware overhead takes
    ~130 KB of the 200 KB total).  Loading the full ~52 KB module set fails
    silently — each failed __import__() partially allocates, then frees,
    leaving the heap MORE fragmented.  On Pico, load only the small set of
    modules that MUST be in sys.modules to prevent crashes in run().
    """
    _gc()
    try:
        import gc as _gc_mod
        _f = _gc_mod.mem_free(); _t = _f + _gc_mod.mem_alloc()
        print("[PRELOAD] start: {} KB free of {} KB total".format(_f // 1024, _t // 1024))
    except Exception:
        pass

    if is_pico:
        # Pico W: ~70 KB free (fragmented).  Full preload silently fails and
        # worsens fragmentation via partial alloc/free churn.
        # Load only the modules that MUST be in sys.modules to avoid a crash
        # inside run() and to allow wifi_supported() to succeed.
        # NOTE: even when WiFi is disabled, the boot pipeline (BME280/AirSensor
        # imports) fragments the heap enough that a 1280-byte allocation for
        # src.app.main fails post-boot.  Load it here while the heap is clean.
        # air, app.main (+ flows + clicks as side-effects) are imported above,
        # before OLED+fonts allocate 53 KB of fragmented heap.
        # Only the remaining smaller modules need to be preloaded here.
        _pico_mods = (
            "src.ui.waiting",             # ~8 KB  — imported inside run(); fails post-boot
            "src.ui.screens.time",        # ~4 KB  — double-click: 1280-byte fail post-boot
            "src.input.button",           # ~3 KB  — imported early in run()
            "src.net.wifi_manager_null",  # 1.3 KB — crash fix: line 252 of run()
            "src.net.net_caps",           # 0.5 KB — wifi_supported() probe in step_wifi
        )
        _diag_lines = []
        for mod in _pico_mods:
            try:
                _f0 = _gc_mod.mem_free()
                __import__(mod)
                _gc()
                _f1 = _gc_mod.mem_free()
                _diag_lines.append("OK {} {}KB".format(mod.split(".")[-1], (_f0 - _f1) // 1024))
            except Exception as _pe:
                _gc()
                _diag_lines.append("FAIL {} {}".format(mod.split(".")[-1], repr(_pe)))
                print("[PRELOAD] FAIL {}: {}".format(mod.split(".")[-1], repr(_pe)))
        try:
            with open("preload_diag.txt", "w") as _df:
                _df.write("\n".join(_diag_lines) + "\n")
        except Exception:
            pass
        del _diag_lines
        try:
            _f = _gc_mod.mem_free(); _t = _f + _gc_mod.mem_alloc()
            print("[PRELOAD] done:  {} KB free of {} KB total".format(_f // 1024, _t // 1024))
        except Exception:
            pass
        _gc()
        return

    # --- ESP32 full preload ---
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
        "src.ui.waiting",      # ~8 KB — imported unconditionally at top of run()
        "src.ui.connection_header",  # ~3 KB — imported at top of run(); GPS constants
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
        # GPS, Compass, and Sailpoint must be preloaded only in turtle mode: they appear
        # late in the turtle sensor carousel, after Device screen's HTTP attempt has
        # fragmented the heap.  In non-turtle mode they are never shown, so loading them
        # wastes ~6-9 KB of preload budget.
    ):
        try:
            __import__(mod)
            _gc()  # coalesce after each import — reduces fragmentation
        except Exception:
            pass

    if turtle_mode:
        for mod in (
            "src.ui.screens.gps",       # GPS status screen (turtle sensor carousel)
            "src.ui.screens.compass",   # compass heading screen (turtle sensor carousel)
            "src.ui.screens.sailpoint", # AS5600 sail angle screen (turtle only)
            # Nav stack: imported post-WiFi via _get_nav()/get_screen("state")
            # when the heap is fragmented — bytecode must be resident by then.
            "src.nav.state_machine",    # ~1 KB — also imported by telemetry on every send
            "src.nav.bearing",          # ~2 KB — great-circle math
            "src.nav.gpsfix",           # ~2 KB — shared GPS fix cache
            "src.nav.pid",              # ~1 KB
            "src.nav.luff",             # ~4 KB — sweep + rolling variance
            "src.nav.heading",          # ~2 KB
            "src.nav.waypoints",        # ~2 KB
            "src.nav.controller",       # ~5 KB — orchestrator
            "src.ui.screens.state",     # ~4 KB — three-circle machine-state screen
        ):
            try:
                __import__(mod)
                _gc()
            except Exception:
                pass

    try:
        _f = _gc_mod.mem_free(); _t = _f + _gc_mod.mem_alloc()
        print("[PRELOAD] done:  {} KB free of {} KB total".format(_f // 1024, _t // 1024))
    except Exception:
        pass

    # Pre-warm font writers: triggers any lazy caches before WiFi runs.
    if oled:
        for attr in ("f_small", "f_med", "f_large",
                     "f_arvo16", "f_arvo20"):
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
# (_pt / _is_esp32 / _is_pico are set in the board-detection block at boot start.)

_early_cfg = None
_preload_needed = False
if _is_esp32 or _is_pico:
    try:
        _early_cfg = load_cfg_dict()
        if _early_cfg is None:
            # Config unreadable — safe defaults differ by platform:
            # ESP32: preload to avoid post-WiFi heap fragmentation crash.
            # Pico W: preload worsens the ~70 KB fragmented heap; skip it.
            _preload_needed = not _is_pico
        else:
            _preload_needed = bool(_early_cfg.get("wifi_enabled", False))
    except Exception:
        _preload_needed = not _is_pico  # same platform-specific safe default

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
    _preload_screens(
        oled,
        is_pico=_is_pico,
        turtle_mode=bool((_early_cfg or {}).get("turtle_mode", False)),
    )
elif _is_pico:
    # WiFi is disabled but we still need the core app modules preloaded.
    # Even without WiFi PHY fragmentation, the boot pipeline (BME280/AirSensor
    # imports) leaves the heap fragmented enough that a 1280-byte allocation for
    # src.app.main fails.  No WiFi PHY pre-activation needed — just the modules.
    _preload_screens(
        oled,
        is_pico=True,
        turtle_mode=bool((_early_cfg or {}).get("turtle_mode", False)),
    )
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
    from src.app.booter import Booter
    booter = Booter(oled) if oled else None
    if booter and _early_cfg and _early_cfg.get("turtle_mode"):
        booter.brand = "turtleOS"
        booter.version = "turtleOS version " + booter.version_num
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
            # Pico W / CYW43: no RTCWDT constraint; allow two retries because the
            # CYW43 driver often reports spurious WRONG_PASSWORD (status=2) on the
            # first cold-boot association attempt before recovering on hard-reset.
            timeout_s=12,
            retry=2 if _is_pico else 0,
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

    _bat = "" if info.get("battery_ok") is not False else " [BAT LOW]"
    wifi_ok = isinstance(wifi_boot, dict) and wifi_boot.get("ok")

    # NTP fallback: dead battery means DS3231 may "sync" with stale data, so
    # treat battery_ok=False the same as a failed sync — override with NTP.
    battery_ok = info.get("battery_ok") is not False
    _ntp_src = ""
    if wifi_ok and not (info.get("synced") and battery_ok):
        _why = "battery dead" if info.get("battery_ok") is False else "no valid DS3231 time"
        print("[RTC] {} — trying NTP".format(_why))
        try:
            from src.app.rtc_sync import sync_system_rtc_from_ntp
            ntp = sync_system_rtc_from_ntp(timeout_s=5)
            if ntp.get("synced"):
                info["synced"] = True
                info["dt_utc"] = ntp.get("dt_utc")
                info["unix"] = ntp.get("unix")
                _ntp_src = " via NTP"
                print("[RTC] NTP sync OK")
            else:
                print("[RTC] NTP failed:", ntp.get("error") or ntp.get("reason"))
        except Exception as _ne:
            print("[RTC] NTP error:", repr(_ne))

    # NOT FOUND only if DS3231 absent and NTP also failed
    if not info.get("detected") and not info.get("synced"):
        return True, "NOT FOUND"

    if not info.get("synced"):
        _r = info.get("reason") or info.get("error") or "?"
        return True, "FOUND – sync fail ({}){}".format(_r, _bat)

    # Synced — fold the time into the result line (keeps output to two lines).
    dt = info.get("dt_utc")
    if not dt or len(dt) < 7:
        return True, "OK{}".format(_ntp_src)

    h, mi = dt[4], dt[5]

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
            tz_hh = abs(tz_min) // 60
            tz_mm = abs(tz_min) % 60
            tz_label = "UTC{}{}".format(tz_sign, tz_hh) if tz_mm == 0 \
                       else "UTC{}{}:{:02d}".format(tz_sign, tz_hh, tz_mm)
            return True, "OK {:02d}:{:02d} local ({}){}{}".format(lh, lmi, tz_label, _bat, _ntp_src)
        except Exception:
            pass

    return True, "OK {:02d}:{:02d} UTC{}{}".format(h, mi, _bat, _ntp_src)


def step_warmup():
    """
    Scan I2C, log found sensors + active capabilities, start warmup.

    NOTE: AirSensor is constructed here (not earlier) to avoid ESP32 heap
    fragmentation — WiFi PHY must allocate before large Python objects.
    """
    global air

    addrs = i2c_scan()
    has_aht   = I2C_ADDR_AHT2X  in addrs   # AHT10/AHT21 — temp + humidity
    has_ens   = I2C_ADDR_ENS160 in addrs   # ENS160       — eco2, tvoc, aqi
    has_scd41 = I2C_ADDR_SCD41  in addrs   # SCD41        — true co2, temp2
    # BME280: 0x76 (SDO=LOW) or 0x77 (SDO=HIGH — also used by BMP280 at alt addr)
    _bme_addr = (I2C_ADDR_BME280_ALT if I2C_ADDR_BME280_ALT in addrs
                 else I2C_ADDR_BME280 if I2C_ADDR_BME280 in addrs
                 else None)
    has_bme   = _bme_addr is not None

    if not (has_aht or has_ens or has_scd41 or has_bme):
        return True, "NO SENSORS DETECTED"

    # Line 2 of 3: inventory of detected sensor chips
    found = []
    if has_bme:   found.append("BME280")
    if has_aht:   found.append("AHT21")
    if has_ens:   found.append("ENS160")
    if has_scd41: found.append("SCD41")
    print("[BOOT] Sensors: {}".format("  ".join(found)))

    if air is None:
        try:
            import sys as _sys, gc as _gcm
            _air_cached = 'src.sensors.air' in _sys.modules
            _f = _gcm.mem_free(); _t = _f + _gcm.mem_alloc()
            print("[AIR] cached={} heap={}/{}KB".format(_air_cached, _f//1024, _t//1024))
            del _sys, _gcm
        except Exception:
            pass
        try:
            _gc()
            _gc()  # extra pass: reclaim anything WiFi left behind
            from src.sensors.air import AirSensor
            # 100 kHz: SCD4X max rated speed; AHT21, ENS160, BME280 all tolerate it.
            air = AirSensor(freq=100_000, bme280_addr=_bme_addr or I2C_ADDR_BME280)
        except Exception as e:
            print("AIR init failed:", repr(e))
            air = None
            return True, "AIR INIT FAIL"

    # _ensure_hw() starts the SCD41 periodic measurement cycle immediately so
    # it has a valid reading ready by the time finish_sampling() is called.
    # It runs at 100 kHz (AirSensor default), which is more forgiving than the
    # 400 kHz boot scan — ENS160 in particular can be missed by a 400 kHz scan
    # on boards with weaker pull-ups but respond fine at 100 kHz.
    try:
        air._ensure_hw()
    except Exception as e:
        print("AIR hw init failed:", repr(e))

    # Determine capabilities from AirSensor's actual driver state after _ensure_hw(),
    # not from the 400 kHz boot scan. A sensor that doesn't ACK at 400 kHz (weak
    # pull-ups, marginal signal) may still be initialised successfully at 100 kHz.
    _ens_ok   = getattr(air, "_ens",   None) is not None
    _aht_ok   = getattr(air, "_aht",   None) is not None
    _scd41_ok = getattr(air, "_scd41", None) is not None
    _bme_ok   = getattr(air, "_bme",   None) is not None

    # Diagnostic: flag any sensor found at 100 kHz but missed in the 400 kHz scan.
    _drv_parts = []
    if _ens_ok:
        _drv_parts.append("ENS160" + ("" if has_ens else "(*100kHz)"))
    else:
        _drv_parts.append("ENS160:FAIL")
    if _aht_ok:
        _drv_parts.append("AHT21")
    else:
        _drv_parts.append("AHT21:FAIL")
    if _scd41_ok:
        _drv_parts.append("SCD41")
    if _bme_ok:
        _drv_parts.append("BME280")
    print("[AIR] drivers: {}".format("  ".join(_drv_parts)))

    caps = []
    if _bme_ok or _aht_ok:
        caps += ["temp", "hum"]
    if _bme_ok:
        caps.append("pressure")
    if _ens_ok:
        caps += ["eco2", "tvoc", "aqi"]
    if _scd41_ok:
        caps += ["co2", "temp2"]

    try:
        warmup_s = float(cfg.get("warmup_seconds", 4.0) if cfg else 4.0)
    except Exception:
        warmup_s = 4.0

    try:
        air.begin_sampling(warmup_seconds=warmup_s, source="boot")
        return True, "{}s warmup | {}".format(int(warmup_s), " ".join(caps))
    except Exception:
        return True, "WARMUP ERROR"  # non-fatal


def step_gps():
    global gps_boot
    ok, detail, info = gps_boot_check(cfg)
    gps_boot = info
    return True, detail


def step_as5600():
    """Probe the AS5600 magnetic angle sensor (sail position, 0x36)."""
    addrs = i2c_scan()
    if I2C_ADDR_AS5600 not in addrs:
        return True, "NOT FOUND"
    try:
        _gc()
        from src.drivers.as5600 import AS5600
        sensor = AS5600(init_i2c())
        if not sensor.is_present:
            return True, "init failed"
        st = sensor.status()
        if st["md"]:
            return True, "OK – magnet detected"
        elif st["mh"]:
            return True, "OK – magnet too strong"
        elif st["ml"]:
            return True, "OK – magnet too weak"
        return True, "OK – no magnet"
    except Exception:
        return True, "ERROR"


def step_compass():
    addrs = i2c_scan()
    if I2C_ADDR_QMC5883 in addrs:
        return True, "QMC5883L OK (0x0D)"
    if I2C_ADDR_HMC5883 in addrs:
        return True, "HMC5883L OK (0x1E)"
    return True, "Compass: NOT FOUND"


def step_servo():
    """
    Probe the servo output.  PWM init always succeeds on ESP32-S3 regardless
    of physical wiring, so the servo_present config flag is authoritative.
    """
    pin = None
    try:
        from src.hal.board import servo_pin as _sp
        pin = _sp()
    except Exception:
        pass

    if pin is None:
        print("[BOOT] Servo: no servo pin on this board")
        return True, "No servo pin"

    is_present = bool(cfg and cfg.get("servo_present", False))
    if not is_present:
        print("[BOOT] Servo: not wired (servo_present=false in config)")
        return True, "Not wired"

    try:
        _gc()
        from src.drivers.servo import Servo
        s = Servo(pin)
        s.deinit()
        print("[BOOT] Servo: OK GPIO{}".format(pin))
        return True, "Servo OK (GPIO{})".format(pin)
    except Exception as e:
        print("[BOOT] Servo: init failed:", repr(e))
        return True, "Servo init FAIL"


# Keep the Pin object alive in a module-level global so GC cannot finalize it
# between boot steps.  On some MicroPython/ESP32-S3 builds, Pin finalization
# resets the GPIO to input mode, immediately extinguishing the LED.
_boot_led_gpio = None   # pin number (int) — used for cleanup
_boot_led_active = 1    # logic level that means "on"
_boot_led_pin = None    # Pin object kept alive to hold the GPIO HIGH/LOW state


def step_led():
    """
    Light the onboard user LED for the remainder of boot.
    Stores only the pin number + active level (ints) so the GC cannot interfere.
    The hardware register holds the LED state; the Python Pin object is discarded.
    Turned off by the cleanup block that follows boot_pipeline().
    """
    global _boot_led_gpio, _boot_led_active, _boot_led_pin
    led_pin = None
    active_val = 1
    try:
        from src.hal.board import user_led_pin as _ulp, user_led_active_value as _ulav
        led_pin = _ulp()
        active_val = _ulav()
    except Exception:
        pass

    if led_pin is None:
        print("[BOOT] LED: no onboard user LED on this board")
        return True, "No onboard LED"

    try:
        from machine import Pin as _Pin
        _boot_led_gpio = led_pin
        _boot_led_active = active_val
        # Hold the Pin object in a module global so GC cannot finalize it.
        # On some MicroPython/ESP32-S3 builds, Pin finalization resets the GPIO
        # to input mode, which turns the LED off immediately.
        _boot_led_pin = _Pin(led_pin, _Pin.OUT)
        _boot_led_pin.value(active_val)
        print("[BOOT] LED: OK GPIO{} (active={})".format(led_pin, active_val))
        return True, "LED OK (GPIO{})".format(led_pin)
    except Exception as e:
        print("[BOOT] LED: failed:", repr(e))
        return True, "LED FAIL"


# Reordered: WiFi earlier (ESP32 heap stability) + API right after WiFi;
# LED lights after API and stays on through the remaining boot steps.
_turtle_boot = bool((_early_cfg or {}).get("turtle_mode", False))

steps = [
    ("Loading config...", step_load_config),
    ("WiFi connect", step_wifi),
    ("Device API check...", step_api),
    ("LED check...", step_led),
    ("RTC clock...", step_rtc),
    ("Warming sensors...", step_warmup),
    ("GPS check...", step_gps),
    ("Compass check...", step_compass),  # I2C address scan only — cheap, no driver import
]
if _turtle_boot:
    steps += [
        ("AS5600 sailpoint...", step_as5600),  # imports AS5600 driver if found on I2C
        ("Servo check...", step_servo),        # imports Servo driver if servo_present=true
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


# Turn off the boot LED now that all steps are done.
if _boot_led_pin is not None:
    try:
        _boot_led_pin.value(1 - _boot_led_active)
    except Exception:
        pass
    _boot_led_pin = None
    _boot_led_gpio = None

# Free the Booter (only needed during boot animation) before show_waiting and run().
# Booter's ThermoBar + WaitingScreen instances hold heap that run() needs.
booter = None
_gc()

# Show waiting immediately after boot (single render)
go_waiting(oled, wifi_boot=wifi_boot, api_boot=api_boot, gps_boot=gps_boot)


# ------------------------------------------------------------
# Preflight: Button HAL must exist (avoid crash loop)
# ------------------------------------------------------------
try:
    import sys as _bsys, gc as _bgc
    _bloglines = ["air={}".format('src.sensors.air' in _bsys.modules),
                  "appmain={}".format('src.app.main' in _bsys.modules),
                  "heap={}KB".format(_bgc.mem_free()//1024)]
    with open("boot_state.txt", "w") as _bf:
        _bf.write(" ".join(_bloglines) + "\n")
    del _bsys, _bgc, _bloglines, _bf
except Exception:
    pass

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
        # Write crash info with minimal heap — repr(e) is cheap, no traceback capture
        try:
            with open("crash.txt", "w") as _cf:
                _cf.write(repr(e))
        except Exception:
            pass
        print("AirBuddy crash:", repr(e))
        raise
else:
    # HAL is broken — show message for 30 s then auto-reset so a redeploy takes effect
    import machine as _machine
    _deadline = time.ticks_add(time.ticks_ms(), 30_000)
    while time.ticks_diff(_deadline, time.ticks_ms()) > 0:
        time.sleep(5)
    _machine.reset()