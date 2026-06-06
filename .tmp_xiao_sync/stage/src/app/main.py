# src/app/main.py — AirBuddy 2.1 core loop (Revised + API two-state patch)

import machine
import time

try:
    from src.hal.board import init_i2c, gps_pins
except Exception:
    init_i2c = None
    gps_pins = None

from src.ui.clicks import (
    gc_collect as _gc,
    reset_and_flush as _reset_and_flush,
)

from src.ui.flows import (
    connectivity_carousel,
    sensor_carousel,
    time_flow,
    selfdestruct_flow,
    sleep_flow,
)

DEBUG_SCREENS = False

# ------------------------------------------------------------
# API icon behavior tuning
# ------------------------------------------------------------
API_SENDING_HOLD_MS = 1200            # 1.2 seconds (visible)


def _resolve_btn_pin_default():
    try:
        import src.hal.board as b
        fn = getattr(b, "btn_pin", None)
        if callable(fn):
            return int(fn())
        if hasattr(b, "BTN_PIN"):
            return int(getattr(b, "BTN_PIN"))
    except Exception:
        pass
    return 15


def _now_ms():
    try:
        return time.ticks_ms()
    except Exception:
        return int(time.time() * 1000)


def _ticks_add(a, b):
    try:
        return time.ticks_add(a, b)
    except Exception:
        return a + b


def _ticks_diff(a, b):
    try:
        return time.ticks_diff(a, b)
    except Exception:
        return a - b


# ============================================================
# MAIN RUN
# ============================================================
def run(
        rtc_synced=None,
        wifi_boot=None,
        api_boot=None,
        oled=None,
        air_sensor=None,
        boot_warmup_started=False,
        rtc_info=None,
        gps_boot=None,
):
    BTN_PIN = _resolve_btn_pin_default()
    from config import load_config
    from src.input.button import AirBuddyButton
    from src.ui.waiting import WaitingScreen

    def init_gps(uart_id=1, baud=9600, tx_pin=8, rx_pin=9):
        try:
            from src.sensors.ublox6gps import Ublox6GPS
            return Ublox6GPS(uart_id=uart_id, baud=baud, tx_pin=tx_pin, rx_pin=rx_pin)
        except Exception as e:
            print("GPS:init skipped:", repr(e))
            return None

    try:
        from src.app.rtc_sync import refresh_ds3231_temp as _refresh_rtc_temp
    except Exception:
        _refresh_rtc_temp = None

    if oled is None:
        try:
            from src.ui.oled import OLED
            oled = OLED()
        except OSError as e:
            if e.args[0] in (5, 19):  # EIO or ENODEV — no I2C device present
                print("[APP] OLED: not found — running headless")
            else:
                print("[APP] OLED: init failed:", repr(e))
            oled = None
        except Exception as e:
            print("[APP] OLED: init failed:", repr(e))
            oled = None

    # ------------------------------------------------------------
    # I2C — create a fresh instance so the peripheral is in a known-good
    # state after the boot pipeline's multiple I2C inits (RTC step,
    # i2c_scan, AirSensor warmup each reinitialise I2C(0) in sequence).
    # Then re-sync AirSensor and its child drivers to this instance so
    # no stale 100 kHz handles linger alongside the new peripheral init.
    # Use air_sensor (the parameter) not air (assigned later at line ~220).
    # ------------------------------------------------------------
    if init_i2c:
        i2c = init_i2c()
    else:
        from machine import I2C, Pin
        i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=100000)

    if air_sensor is not None:
        air_sensor._i2c = i2c
        for _attr in ('_aht', '_ens', '_aht10'):
            _drv = getattr(air_sensor, _attr, None)
            if _drv is not None:
                try:
                    _drv.i2c = i2c
                except Exception:
                    pass
        # Retry any driver that silently failed during boot warmup
        try:
            air_sensor._ensure_hw()
        except Exception:
            pass

    _ina_dev = None
    try:
        from src.drivers.ina219 import INA219 as _INA219
        _gc()
        _ina_dev = _INA219(i2c, auto_init=True)
        if not _ina_dev.is_present:
            _ina_dev = None
    except Exception:
        _ina_dev = None

    rtc = rtc_info if isinstance(rtc_info, dict) else {}

    # ------------------------------------------------------------
    # GPS INIT
    # ------------------------------------------------------------
    if gps_pins:
        GPS_UART_ID, GPS_BAUD, GPS_TX_PIN, GPS_RX_PIN = gps_pins()
    else:
        GPS_UART_ID, GPS_BAUD, GPS_TX_PIN, GPS_RX_PIN = (1, 9600, 8, 9)

    _gps_cfg = load_config() or {}
    if _gps_cfg.get("gps_enabled", False) and init_gps is not None:
        try:
            gps = init_gps(
                uart_id=GPS_UART_ID,
                baud=GPS_BAUD,
                tx_pin=GPS_TX_PIN,
                rx_pin=GPS_RX_PIN,
            )
        except Exception:
            gps = None
        if gps is not None:
            try:
                gps.configure_mode(_gps_cfg.get("turtle_mode", False))
            except Exception as e:
                print("[GPS] configure:", repr(e))
    else:
        gps = None

    try:
        from src.ui.connection_header import GPS_NONE, GPS_INIT, GPS_FIXED
    except Exception:
        GPS_NONE, GPS_INIT, GPS_FIXED = 0, 1, 2
    try:
        from src.ui import connection_header as _ch_mod
    except Exception:
        _ch_mod = None
    gps_state = GPS_NONE

    def _probe_gps():
        nonlocal gps_state
        if gps is None:
            gps_state = GPS_NONE
            return
        # Hardware present — check UART buffer for incoming bytes.
        # Ublox6GPS exposes gps.uart, not gps.any() directly.
        gps_state = GPS_INIT
        try:
            uart = getattr(gps, "uart", None)
            if uart is not None and uart.any():
                gps_state = GPS_FIXED
        except Exception:
            pass

    # ------------------------------------------------------------
    # CORE OBJECTS
    # ------------------------------------------------------------
    btn = AirBuddyButton(
        gpio_pin=BTN_PIN,
        click_window_s=0.8,
        debounce_ms=45,
    )

    waiting = WaitingScreen()

    turtle_waiting_scr = None
    if _gps_cfg.get("turtle_mode", False) and oled is not None:
        try:
            _gc()
            from src.ui.screens.turtle_waiting import TurtleWaitingScreen
            turtle_waiting_scr = TurtleWaitingScreen(oled)
            _gc()
        except Exception as e:
            print("[APP] turtle_waiting init failed:", repr(e))

    # Only probe the WiFi PHY when WiFi is actually enabled.
    # On Pico W, network.WLAN(STA_IF) initialises the CYW43 driver
    # which allocates ~30 KB of Python heap — fatal when heap is tight.
    # If wifi_enabled=False we will use NullWiFiManager anyway, so skip.
    _hw_wifi = False
    if _gps_cfg.get("wifi_enabled", False):
        try:
            from src.net.net_caps import wifi_supported as _wifi_supported
            _hw_wifi = _wifi_supported()
        except Exception:
            _hw_wifi = False

    _boot_wifi_ok = isinstance(wifi_boot, dict) and wifi_boot.get("ok")

    # On Pico/CYW43 there is no RTCWDT risk from re-entering the WiFi driver
    # after a failed boot connection.  Always create WiFiManager on Pico if WiFi
    # is supported and enabled — the driver can reconnect from the main loop and
    # screens, and _bg_tick() will update status["wifi_ok"] correctly.
    # On ESP32: gate on _boot_wifi_ok to avoid RTCWDT from broken driver state.
    try:
        from src.hal.platform import platform_tag as _plt_tag
        _is_esp32_loop = (_plt_tag() == "esp32")
    except Exception:
        _is_esp32_loop = False

    _wifi_manager_ok = _boot_wifi_ok if _is_esp32_loop else True
    wifi = None
    if _hw_wifi and _gps_cfg.get("wifi_enabled", False) and _wifi_manager_ok:
        try:
            from src.net.wifi_manager import WiFiManager
            wifi = WiFiManager()
        except Exception as e:
            # C WiFi driver OOM or broken state from boot — run offline.
            print("[WIFI] init failed, running offline:", repr(e))
    if wifi is None:
        from src.net.wifi_manager_null import NullWiFiManager
        wifi = NullWiFiManager()
        if not _gps_cfg.get("wifi_enabled", False):
            try:
                from src.ui import connection_header as _ch
                _ch.set_wifi_enabled(False)
            except Exception:
                pass

    air = air_sensor

    # Inject the shared I2C bus so AirSensor doesn't create a second one.
    # On ESP32, two I2C() objects on the same bus ID corrupt the bus → RTCWDT.
    if air is not None and getattr(air, '_i2c', None) is None:
        air._i2c = i2c

    # ------------------------------------------------------------
    # STATUS STATE
    # ------------------------------------------------------------
    initial_wifi_ok = bool(isinstance(wifi_boot, dict) and wifi_boot.get("ok"))
    initial_api_ok = bool(isinstance(api_boot, dict) and api_boot.get("ok"))

    status = {
        "wifi_ok": initial_wifi_ok,
        "api_ok": initial_api_ok,
        "api_sending": False,
        "gps_on": GPS_NONE,
    }

    api_sending_until_ms = 0

    # ------------------------------------------------------------
    # TELEMETRY
    # ------------------------------------------------------------
    telemetry = None
    telemetry_started = False

    def _get_rtc_fresh():
        # Force a fresh DS3231 read into rtc right before telemetry sends.
        # _refresh_rtc_temp with force=True bypasses the 70s throttle so the
        # payload always contains the latest chip temperature regardless of
        # which screen is currently showing (including sleep / idle).
        if _refresh_rtc_temp is not None:
            try:
                _refresh_rtc_temp(i2c, rtc, force=True)
            except Exception:
                pass
        return rtc

    def start_telemetry_if_ready(cfg):
        nonlocal telemetry, telemetry_started
        if telemetry_started:
            return
        if not status["wifi_ok"]:
            return
        try:
            from src.app.telemetry_state import TelemetryState
            telemetry = TelemetryState(
                air_sensor=air,
                rtc_info_getter=_get_rtc_fresh,
                wifi_manager=wifi,
                gps=gps,
                battery_sensor=_ina_dev,
            )
            telemetry_started = True
            print("[TELEMETRY] Started.")
        except Exception as e:
            print("[TELEMETRY] Failed to start:", repr(e))

    def _refresh_api_flags(now_ms):
        nonlocal api_sending_until_ms, status

        try:
            status["api_sending"] = (_ticks_diff(now_ms, api_sending_until_ms) < 0)
        except Exception:
            status["api_sending"] = False

    def tick_telemetry(cfg):
        nonlocal status, api_sending_until_ms
        if not telemetry:
            return

        now_ms = _now_ms()
        try:
            result = telemetry.tick(cfg)

            if result is True:
                api_sending_until_ms = _ticks_add(now_ms, int(API_SENDING_HOLD_MS))
                status["api_ok"] = True

            elif result is False:
                api_sending_until_ms = _ticks_add(now_ms, int(API_SENDING_HOLD_MS))
                status["api_ok"] = False

            # None => not due, no change to api_ok

        except Exception as e:
            print("[TELEMETRY] Tick error:", repr(e))
            status["api_ok"] = False

        _refresh_api_flags(now_ms)

    # ------------------------------------------------------------
    # SCREEN CACHE + RUNNER
    # ------------------------------------------------------------
    screens = {}

    def run_screen(name, **kwargs):
        """
        Debuggable screen invoker.
        - Tries show_live(btn=...) if available
        - Else tries show(...)
        - Prints why it failed (MemoryError, import error, signature mismatch, etc.)
        Returns action for show_live, else None.
        """
        scr = get_screen(name)
        if scr is None:
            if DEBUG_SCREENS:
                print("[run_screen] missing:", name)
            return None

        # show_live preferred
        if hasattr(scr, "show_live"):
            try:
                return scr.show_live(**kwargs)
            except TypeError as e:
                if DEBUG_SCREENS:
                    print("[run_screen] show_live TypeError:", name, repr(e))
            except MemoryError as e:
                print("[run_screen] show_live MemoryError:", name, repr(e))
            except Exception as e:
                if DEBUG_SCREENS:
                    print("[run_screen] show_live error:", name, repr(e))

        # fallback show()
        if hasattr(scr, "show"):
            try:
                return scr.show(**kwargs)
            except TypeError as e:
                if DEBUG_SCREENS:
                    print("[run_screen] show TypeError:", name, repr(e))
            except MemoryError as e:
                print("[run_screen] show MemoryError:", name, repr(e))
            except Exception as e:
                if DEBUG_SCREENS:
                    print("[run_screen] show error:", name, repr(e))

        return None

    def get_screen(name):
        """
        IMPORTANT FIX:
        - If a screen previously failed and cached None, we RETRY.
        - We also print the exception so we can see *why* temp/co2/tvoc failed.
        """
        if oled is None:
            return None  # OLED not initialised — all display screens unavailable

        if name in screens and screens[name] is not None:
            return screens[name]

        # If it was cached as None, remove it so we can retry.
        if name in screens and screens[name] is None:
            try:
                del screens[name]
            except Exception:
                pass

        try:
            _gc()   # collect before import + instantiation
            # NOTE: keep imports inside to reduce boot RAM.
            if name == "device":
                from src.ui.screens.device import DeviceScreen
                screens[name] = DeviceScreen(oled)

            elif name == "gps":
                from src.ui.screens.gps import GPSScreen
                screens[name] = GPSScreen(oled)

            elif name == "wifi":
                from src.ui.screens.wifi import WiFiScreen
                screens[name] = WiFiScreen(oled)

            elif name == "online":
                from src.ui.screens.online import OnlineScreen
                screens[name] = OnlineScreen(oled)

            elif name == "logging":
                from src.ui.screens.logging import LoggingScreen
                screens[name] = LoggingScreen(oled)

            elif name == "co2":
                from src.ui.screens.co2 import CO2Screen
                screens[name] = CO2Screen(oled)

            elif name == "eco2":
                from src.ui.screens.eco2 import eCO2Screen
                screens[name] = eCO2Screen(oled)

            elif name == "tvoc":
                from src.ui.screens.tvoc import TVOCScreen
                screens[name] = TVOCScreen(oled)

            elif name == "temp":
                from src.ui.screens.temp import TempScreen
                screens[name] = TempScreen(oled, i2c=i2c, status=status, rtc_info=rtc)

            elif name == "temp2":
                from src.ui.screens.temp2 import Temp2Screen
                screens[name] = Temp2Screen(oled, i2c=i2c, status=status, rtc_info=rtc)

            elif name == "summary":
                from src.ui.screens.summary import SummaryScreen
                screens[name] = SummaryScreen(oled)

            elif name == "time":
                from src.ui.screens.time import TimeScreen
                # cfg is created in the loop, so it must exist when called
                screens[name] = TimeScreen(
                    oled,
                    cfg,
                    wifi_manager=wifi,
                    rtc_info=rtc,
                    ds3231=None,
                    status=status,
                )

            elif name == "selfdestruct":
                from src.ui.screens.selfdestruct import SelfDestructScreen
                screens[name] = SelfDestructScreen(oled)

            elif name == "compass":
                from src.ui.screens.compass import CompassScreen
                screens[name] = CompassScreen(oled, i2c=i2c,
                                              offset_deg=cfg.get("compass_offset_deg", 0))

            elif name == "sailpoint":
                from src.ui.screens.sailpoint import SailpointScreen
                screens[name] = SailpointScreen(oled, i2c=i2c,
                                                offset_deg=cfg.get("sailpoint_offset_deg", 0))

            elif name == "servo":
                from src.ui.screens.servo import ServoScreen
                try:
                    from src.hal.board import servo_pin as _servo_pin
                    _sp = _servo_pin()
                except Exception:
                    _sp = None
                screens[name] = ServoScreen(oled, servo_pin=_sp)

            elif name == "battery":
                from src.ui.screens.battery import BatteryScreen
                screens[name] = BatteryScreen(oled, i2c=i2c, ina=_ina_dev)

            elif name == "destination":
                from src.ui.screens.destination import DestinationScreen
                screens[name] = DestinationScreen(oled)

            elif name == "sleep":
                from src.ui.screens.sleep import SleepScreen
                screens[name] = SleepScreen(oled)

            elif name == "turtle_waiting":
                from src.ui.screens.turtle_waiting import TurtleWaitingScreen
                screens[name] = TurtleWaitingScreen(oled)

            else:
                screens[name] = None

        except MemoryError as e:
            print("[get_screen] MemoryError:", name, repr(e))
            # Do NOT cache None permanently; allow retry later.
            screens.pop(name, None)
            _gc()
            return None

        except Exception as e:
            print("[get_screen] failed:", name, repr(e))
            # Do NOT cache failure permanently; allow retry later.
            screens.pop(name, None)
            _gc()
            return None

        _gc()
        return screens.get(name)

    # ------------------------------------------------------------
    # BACKGROUND TICK — called by every blocking flow/screen
    # ------------------------------------------------------------
    # _cfg_cell[0] is updated at the top of each main-loop iteration so
    # _bg_tick always sees a fresh config without calling load_config() itself.
    # Calling load_config() every 500 ms allocates and frees JSON dicts that
    # fragment the heap even before telemetry fires.
    _cfg_cell = [{}]

    def _bg_tick():
        try:
            status["wifi_ok"] = bool(wifi.is_connected())
            start_telemetry_if_ready(_cfg_cell[0])
            _gc()
            tick_telemetry(_cfg_cell[0])
            _gc()
        except Exception:
            pass

    # ========================================================
    # Idle callback — defined ONCE here, uses _cfg_cell so
    # it always sees the latest config without being recreated
    # each main-loop iteration (which would cost heap per call).
    # ========================================================
    def _idle(now_ms):
        try:
            status["wifi_ok"] = bool(wifi.is_connected())
        except Exception:
            status["wifi_ok"] = False

        if _refresh_rtc_temp is not None:
            try:
                _refresh_rtc_temp(i2c, rtc)
            except Exception:
                pass

        start_telemetry_if_ready(_cfg_cell[0])
        tick_telemetry(_cfg_cell[0])

        return {
            "wifi_ok": status["wifi_ok"],
            "gps_on": status["gps_on"],
            "api_ok": status["api_ok"],
            "api_sending": status["api_sending"],
        }

    # ============================================================
    # MAIN LOOP
    # ============================================================
    while True:
        cfg = load_config() or {}
        _cfg_cell[0] = cfg          # keep background tick in sync

        # --- WiFi live refresh ---
        try:
            status["wifi_ok"] = bool(wifi.is_connected())
        except Exception:
            status["wifi_ok"] = False

        # --- GPS detection ---
        try:
            if cfg.get("gps_enabled", False):
                _probe_gps()
                status["gps_on"] = gps_state
            else:
                status["gps_on"] = GPS_NONE
        except Exception:
            status["gps_on"] = GPS_NONE
        if _ch_mod:
            try:
                _ch_mod.set_gps_state(status["gps_on"])
            except Exception:
                pass

        # --- RTC temp refresh (throttled internally to ~70s) ---
        if _refresh_rtc_temp is not None:
            try:
                _refresh_rtc_temp(i2c, rtc)
            except Exception:
                pass

        # --- Telemetry start ---
        start_telemetry_if_ready(cfg)

        # keep api flags fresh even between sends
        _refresh_api_flags(_now_ms())

        # Collect before entering the blocking waiting-screen loop to
        # reclaim any temporaries from load_config() and status updates.
        _gc()

        # --- Waiting screen ---
        if turtle_waiting_scr is not None:
            action = turtle_waiting_scr.show_live(
                btn=btn,
                on_idle=_idle,
                idle_every_ms=4000,
                status=status,
            )
        else:
            action = waiting.show_live(
                oled,
                btn,
                line="Know thy air...",
                animate=False,
                wifi_ok=status["wifi_ok"],
                gps_on=status["gps_on"],
                api_ok=status["api_ok"],
                on_idle=_idle,
                idle_every_ms=4000,
            )

        if action:
            print("[CLICK]", action)

        # --- Click routing ---
        if action == "triple":
            screens.clear(); _gc()
            connectivity_carousel(
                btn, oled, status, cfg, wifi,
                None, None, gps,
                get_screen,
                tick_fn=_bg_tick,
                telemetry=telemetry,
                device_info=api_boot,
            )
            continue

        if action == "single":
            screens.clear(); _gc()
            # If temp/co2/tvoc missing, the improved get_screen() will now PRINT WHY.
            sensor_carousel(btn, oled, air, get_screen, tick_fn=_bg_tick, gps=gps, cfg=cfg)
            continue

        if action == "double":
            screens.clear(); _gc()
            try:
                import gc as _gc_mod
                print("[TIME] opening time screen | heap free: {} KB".format(_gc_mod.mem_free() // 1024))
            except Exception:
                print("[TIME] opening time screen")
            time_flow(btn, oled, cfg, wifi, None, get_screen, status=status, tick_fn=_bg_tick)
            continue

        if action == "quad":
            screens.clear(); _gc()
            if cfg.get("joke_mode", False):
                selfdestruct_flow(btn, oled, get_screen, tick_fn=_bg_tick)
            else:
                scr = get_screen("turtle_waiting")
                if scr is not None:
                    scr.show_live(btn=btn, tick_fn=_bg_tick, status=status)
            continue

        if action == "sleep":
            screens.clear(); _gc()
            sleep_flow(btn, oled, get_screen, tick_fn=_bg_tick)
            continue

        _reset_and_flush(btn)
        _gc()