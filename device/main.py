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


# ----------------------------
# OLED init
# ----------------------------
def init_oled():
    try:
        from src.ui.oled import OLED

        # 0 for 0.96" SSD1306
        # 2 or 4 for many 1.3" SH1106 modules
        return OLED(col_offset=2)

    except Exception as e:
        print("OLED:init failed:", repr(e))
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

        start = time.ticks_ms()
        seen = False
        while time.ticks_diff(time.ticks_ms(), start) < 1200:
            try:
                if gps.any():
                    b = gps.read(64) or b""
                    if b:
                        seen = True
                        break
            except Exception:
                pass
            time.sleep_ms(50)

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
            line="Know your air...",
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
oled = init_oled()


def _preload_screens(oled):
    """
    Import screen module bytecodes while heap is still clean (pre-WiFi).
    Also pre-warm font metric paths on each writer.
    Both operations must complete before step_wifi() runs.

    ESP32 ONLY — on Pico there is no WiFi PHY to fragment the heap, so
    preloading all these modules upfront just wastes RAM and causes the
    AirSensor allocation to fail.  Call _gc() instead on Pico.
    """
    _gc()

    # Pre-load bytecode for all commonly-used screens.
    # After WiFi fragments the heap these imports would fail with MemoryError.
    # With modules already in sys.modules, get_screen() only allocates the instance.
    # _gc() between each import helps coalesce free blocks on a tight heap.
    for mod in (
        # Core app modules — imported after WiFi; must be pre-loaded
        "src.app.main",
        "src.ui.flows",
        "src.ui.clicks",
        # Screen modules — all must be in sys.modules before WiFi fragments the heap
        "src.ui.screens.co2",
        "src.ui.screens.tvoc",
        "src.ui.screens.temp",
        "src.ui.screens.summary",
        "src.ui.screens.time",
        "src.ui.screens.wifi",
        "src.ui.screens.online",
        "src.ui.screens.logging",
        "src.ui.screens.device",
        "src.ui.screens.gps",
        "src.ui.screens.sleep",
        "src.ui.screens.selfdestruct",
        "src.ui.screens.frowny",
    ):
        try:
            __import__(mod)
            _gc()  # coalesce after each import — reduces fragmentation
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


# _preload_screens is ESP32-only: on Pico/RP2040 there is no WiFi PHY
# allocation that fragments the heap, so loading ~15 modules upfront
# burns most of the available RAM before AirSensor gets a chance.
try:
    from src.hal.platform import platform_tag as _platform_tag
    _is_esp32 = (_platform_tag() == "esp32")
except Exception:
    _is_esp32 = False  # safe default: skip preload

if _is_esp32:
    _preload_screens(oled)
else:
    _gc()  # just collect on Pico — lazy imports work fine without WiFi PHY

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

        ok, ip, status = wifi.connect(
            cfg.get("wifi_ssid", ""),
            cfg.get("wifi_password", ""),
            timeout_s=8,
            retry=1
        )

        wifi_boot = {"ok": bool(ok), "supported": True, "ip": ip, "status": status}
        return True, ("OK" if ok else "FAIL")

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

    if not has_air:
        return True, "NO SENSORS DETECTED"

    if air is None:
        try:
            _gc()
            _gc()  # extra pass: reclaim anything WiFi left behind
            from src.sensors.air import AirSensor
            air = AirSensor()
        except Exception as e:
            print("AIR init failed:", repr(e))
            air = None
            return True, "AIR INIT FAIL"

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
    ("Connecting to WiFi...", step_wifi),
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