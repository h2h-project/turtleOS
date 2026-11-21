# main.py (device root) — turtleOS / airOS boot pipeline (XIAO ESP32-S3)
# Boot -> Waiting -> src/app/main.run()
#
#  - Boot guard sampled first (hold the button at power-on for the REPL)
#  - WiFi runs before AirSensor and other heavy allocations
#  - Each boot step is shown on the OLED (Booter); failures are held readable
#  - If btn_pin is missing in the HAL, show a message and reset instead of
#    crash-looping

from machine import RTC
import time

from src.hal.board import init_i2c, gps_pins

# ----------------------------
# Boot guard — EARLY sample
# ----------------------------
# Sample the button here, before board detection, the two I2C bus scans and
# init_oled() — all of which used to run first, so "hold the button at
# power-on" was really "hold it through MicroPython startup + imports + I2C
# scans + 2 s more", with no on-screen feedback until well after the user
# had let go. This early call blocks ONLY while the button is actually held
# (instant return otherwise), so a normal boot pays nothing. The OLED debug
# screen + wait loop still run later, once init_oled() exists.
_bg_debug = False
try:
    from src.app.boot_guard import check_hold_early
    _bg_debug = bool(check_hold_early())
except Exception as _bg_e:
    print("[BOOT] boot_guard early check error:", repr(_bg_e))

# ----------------------------
# Watchdog — EARLY
# ----------------------------
# Covers the boot pipeline at 120 s (fed once per boot step); tightened to
# watchdog_timeout_s just before run(). Not started in debug mode or with
# watchdog_enabled false; a watchdog left running by the app before a soft
# reset is relaxed instead. See src/app/watchdog.py.
try:
    from src.app import watchdog as _watchdog
    _watchdog.boot(debug=_bg_debug)
except Exception as _wd_e:
    _watchdog = None
    print("[BOOT] watchdog init error:", repr(_wd_e))

# ----------------------------
# Servo power switch — EARLY
# ----------------------------
# The SSR's EN pin (GPIO40) floats until something drives it. Pull it low
# (servo rail off) right after the boot guard; run()'s background tick then
# applies servo_power_mode.
try:
    from src.drivers import servo_power as _servo_power
    _servo_power.init()
except Exception as _sp_e:
    print("[BOOT] servo power init error:", repr(_sp_e))

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


def i2c_scan_ext():
    """One-shot scan of the I2C_EXT bus (plug-in sensors). [] when the board
    has no second bus or the scan fails."""
    try:
        from src.hal.board import init_i2c_ext
        i2c = init_i2c_ext()
        if i2c is None:
            return []
        return i2c.scan() or []
    except Exception:
        return []


def _chip_id(addr, reg=0xD0):
    """Read a Bosch-style chip-ID register; None on error. Used to tell the
    BMP180 (0x55) from a BME280 (0x60) / BMP280 (0x58) — they share 0x77."""
    try:
        return init_i2c().readfrom_mem(addr, reg, 1)[0]
    except Exception:
        return None


# Common I2C addresses we care about
I2C_ADDR_OLED    = 0x3C
I2C_ADDR_RTC     = 0x68
I2C_ADDR_ENS160  = 0x53
I2C_ADDR_AHT2X   = 0x38  # AHT10/AHT20/AHT21
I2C_ADDR_SCD41   = 0x62  # SCD40/SCD41 true CO2 sensor
I2C_ADDR_BME280     = 0x76  # BME280 temp + humidity + pressure (SDO=LOW)
I2C_ADDR_BME280_ALT = 0x77  # BME280 alternate address (SDO=HIGH)
I2C_ADDR_QMC5883 = 0x0D  # QMC5883L — GY-87 on-board mag (via bypass) or standalone GY-271, see step_imu()
I2C_ADDR_HMC5883 = 0x1E  # HMC5883L — same roles as above (genuine Honeywell part)
I2C_ADDR_QMC5883P = 0x2C  # QMC5883P — newer QST mag on GY-87/GY-271 clones, same roles
I2C_ADDR_AS5600  = 0x36  # AS5600 magnetic angle sensor (sail position)
I2C_ADDR_MPU6050 = 0x68  # MPU6050 IMU on the GY-87 — factory address; only probed on I2C_EXT (0x68 on I2C_SYS is the RTC)
I2C_ADDR_MPU6050_ALT = 0x69  # MPU6050 with AD0 strapped high
I2C_ADDR_BMP180  = 0x77  # BMP180 barometer on the GY-87 — shares 0x77 with BME280-alt, see step_warmup()

_I2C_NAMES = {
    0x0D: "QMC5883L",
    0x1E: "HMC5883L",
    0x2C: "QMC5883P",
    0x36: "AS5600",
    0x38: "AHT21",
    0x3C: "OLED",
    0x40: "INA219",
    0x53: "ENS160",
    0x62: "SCD41",
    0x68: "DS3231",       # on I2C_SYS; the same address is the MPU6050 on I2C_EXT
    0x69: "MPU6050",
    0x76: "BME280",
    0x77: "BMP180/BME280",
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
        if e.args[0] in (110, 116):  # ETIMEDOUT (Linux 110 / ESP-IDF newlib 116)
            # The OLED ACKs the scan but stalls the bus mid-write: a hung
            # controller (power-cycle it) or a marginal SDA/SCL/VCC contact.
            print("[BOOT] OLED: 0x3C not responding (I2C timeout) — check wiring/power; running headless")
        elif e.args[0] in (5, 19):  # EIO, ENODEV — no I2C device present
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
                print("[RTC] battery fault: OSF set — DS3231 lost power (replace its coin cell)")
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
def _wait_socket_readable(s, tick_fn, deadline_ms, poll_ms=250):
    """
    Block until `s` has data to read, calling tick_fn() every poll_ms while
    it waits — used so the API check's boot-step DotTicker keeps animating
    through what is otherwise one long blocking network wait (DNS + connect
    + server round-trip), the same way step_wifi/gps_boot_check tick from
    their own poll loops. select.poll() only *observes* readiness; it never
    consumes bytes, so it's safe to interleave with the normal blocking
    socket reads that follow.
    """
    import uselect as select

    poller = select.poll()
    poller.register(s, select.POLLIN)
    try:
        while True:
            if poller.poll(poll_ms):
                return
            if tick_fn:
                tick_fn()
            if time.ticks_diff(deadline_ms, time.ticks_ms()) <= 0:
                raise OSError("timeout waiting for API response")
    finally:
        poller.unregister(s)


def _api_get_ticked(url, headers, timeout_s=6, tick_fn=None):
    """
    Minimal GET, equivalent to urequests.get(), but waits for the socket to
    become readable via _wait_socket_readable() before each blocking read so
    tick_fn() runs throughout the wait instead of the caller staring at a
    frozen 0-dot ticker for the whole request. Kept separate from
    src/lib/urequests.py (used elsewhere for telemetry) so this doesn't risk
    that shared path — this is only ever used for the boot-time API check.
    """
    import usocket as socket

    proto, _, host, path = url.split("/", 3)
    if proto != "http:":
        raise ValueError("Unsupported protocol: " + proto)
    port = 80
    if ":" in host:
        host, port = host.split(":", 1)
        port = int(port)

    deadline_ms = time.ticks_add(time.ticks_ms(), int(timeout_s * 1000))

    # Tick once before DNS + connect — both are blocking with no callback
    # hook, so without this the dot ticker sits frozen at zero dots for
    # however long that takes instead of showing its first dot right away.
    if tick_fn:
        try:
            tick_fn()
        except Exception:
            pass

    ai = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0]
    s = socket.socket(ai[0], ai[1], ai[2])
    s.settimeout(timeout_s)

    try:
        s.connect(ai[-1])

        s.write(b"GET /%s HTTP/1.0\r\nHost: %s\r\n" % (path.encode(), host.encode()))
        for k, v in headers.items():
            s.write(("%s: %s\r\n" % (k, v)).encode())
        s.write(b"\r\n")

        _wait_socket_readable(s, tick_fn, deadline_ms)
        l = s.readline()
        l = l.split(None, 2)
        status = int(l[1])

        while True:
            _wait_socket_readable(s, tick_fn, deadline_ms)
            l = s.readline()
            if not l or l == b"\r\n":
                break

        _wait_socket_readable(s, tick_fn, deadline_ms)
        body = s.read()

        class _TickedResponse:
            def close(self):
                pass

        resp = _TickedResponse()
        resp.status_code = status
        resp.text = str(body, "utf-8") if body else ""
        return resp
    finally:
        s.close()


def api_device_lookup(cfg, tick_fn=None):
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

        headers = {
            "X-Device-Id": device_id,
            "X-Device-Key": device_key,
            "Accept": "application/json",
            "Connection": "close",
        }

        print("API lookup: GET", url)

        r = _api_get_ticked(url, headers=headers, timeout_s=6, tick_fn=tick_fn)

        code = getattr(r, "status_code", None)
        print("API lookup: HTTP", code)

        if code != 200:
            info["http_status"] = code
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

        # Mission (missions_tb): short_name is the OLED-friendly label used on
        # the waiting/device screens; full_name is the long form. Tolerant of
        # assignment.mission / top-level mission / flat payloads.
        mis = asg.get("mission") or data.get("mission") or {}
        info["mission_short_name"] = str(
            (mis.get("short_name") if isinstance(mis, dict) else None)
            or data.get("mission_short_name")
            or ""
        )
        info["mission_full_name"] = str(
            (mis.get("full_name") if isinstance(mis, dict) else None)
            or data.get("mission_full_name")
            or data.get("mission_name")
            or ""
        )
        # Aliases matching the config key names step_api() writes.
        info["mission_dest_short_name"] = info["mission_short_name"]
        info["mission_dest_full_name"] = info["mission_full_name"]

        # Mission destination coords (missions_tb.target_*). Tolerant of
        # nested mission.target_lat / flat mission_target_lat. Only kept when
        # both are finite numbers and in range.
        try:
            _mlat = (mis.get("target_lat") if isinstance(mis, dict) else None)
            if _mlat is None:
                _mlat = data.get("mission_target_lat")
            _mlon = (mis.get("target_lng") if isinstance(mis, dict) else None)
            if _mlon is None:
                _mlon = data.get("mission_target_lng")
            if _mlat is not None and _mlon is not None:
                _mlat = float(_mlat); _mlon = float(_mlon)
                if -90.0 <= _mlat <= 90.0 and -180.0 <= _mlon <= 180.0:
                    info["mission_destination"] = [_mlat, _mlon]
        except Exception:
            pass

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
        _mis = info.get("mission_short_name") or info.get("mission_full_name") or ""
        print("API lookup: {} is connected!".format(_dname))
        print("[BOOT] {}: {}".format("Turtle" if cfg.get("turtle_mode", True) else "Device", _dname))
        if _mis:
            print("[BOOT] Mission: {}".format(_mis))
            _detail = "{} / {}".format(_dname, _mis)
        else:
            # Device confirmed but no mission came back — surface it clearly.
            print("[BOOT] Mission: FAILED to retrieve (none in API response)")
            _detail = "{} / no mission".format(_dname)
        return True, _detail, info

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
            from src.sensors.xiao_gnss import GnssModule
            gps = GnssModule(uart_id=GPS_UART_ID, baud=GPS_BAUD, tx_pin=GPS_TX_PIN, rx_pin=GPS_RX_PIN)
        except Exception as e:
            print("GPS:init skipped:", repr(e))
            gps = None
        if gps is None:
            info["ok"] = False
            info["detected"] = False
            return True, "GPS not found", info

        # Presence-only check: just wait for any bytes on the UART — no read needed.
        # Max 5 seconds. Avoids uart.read(n) blocking on inter-character timeouts.
        # Animates the shared per-step ticker (see step_wifi) from this poll loop.
        start = time.ticks_ms()
        seen = False
        while time.ticks_diff(time.ticks_ms(), start) < 5000:
            try:
                if gps.uart.any():
                    seen = True
                    break
            except Exception:
                pass
            if booter and booter.step_ticker:
                booter.step_ticker.tick()
            time.sleep_ms(100)

        info["detected"] = bool(seen)
        info["ok"] = bool(seen)
        return True, ("OK" if seen else "GPS not found"), info

    except Exception:
        info["ok"] = False
        info["detected"] = False
        return True, "GPS not found", info
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
# Lightweight, direct config.json read for turtle_mode — load_config() and
# _early_cfg aren't populated until later in the boot sequence, but the
# wake-up banner needs to know which brand ("Turtle"/"turtleOS" vs "airOS")
# to log before that point.
try:
    import json as _boot_json
    with open("config.json") as _boot_cfg_f:
        _boot_turtle_mode = bool(_boot_json.load(_boot_cfg_f).get("turtle_mode", True))
except Exception:
    _boot_turtle_mode = True

print("[BOOT] Turtle is waking up..." if _boot_turtle_mode else "[BOOT] airOS is waking up...")
try:
    from src.app.booter import VERSION_NUM as _boot_version_num
    _boot_brand = "turtleOS" if _boot_turtle_mode else "airOS"
    print("[BOOT] " + _boot_brand + " version " + _boot_version_num)
except Exception:
    pass
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

# turtleOS runs on the XIAO ESP32-S3 only; src/hal/board.py always uses its
# pin map. The detected tag is still logged, so a turtle whose firmware
# doesn't identify as a XIAO shows up in the boot log.
try:
    from src.hal.platform import platform_tag as _platform_tag
    print("[BOOT] Board: {} (HAL: board_xiao_esp32_s3)".format(_platform_tag()))
except Exception:
    print("[BOOT] Board: unknown (HAL: board_xiao_esp32_s3)")

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

# I2C_EXT — the plug-in bus (GY-87). Labels differ from I2C_SYS: 0x68 here
# is the MPU6050, never the RTC.
try:
    from src.hal.board import i2c_ext_pins as _i2c_ext_pins_fn
    _de = _i2c_ext_pins_fn()
    if _de is not None:
        print("[BOOT] I2C_EXT: I2C({}) SDA=GPIO{} SCL=GPIO{} {}kHz".format(
            _de[0], _de[2], _de[1], _de[3] // 1000))
        _de_found = i2c_scan_ext()
        _de_names = {0x68: "MPU6050", 0x69: "MPU6050(AD0)", 0x0D: "QMC5883L", 0x1E: "HMC5883L", 0x2C: "QMC5883P", 0x77: "BMP180"}
        if _de_found:
            print("[BOOT] I2C_EXT scan: [{}]".format(", ".join(
                "{} {}".format(hex(a), _de_names[a]) if a in _de_names else hex(a) for a in _de_found)))
        else:
            print("[BOOT] I2C_EXT scan: no devices (GY-87 not wired?)")
        del _de_found, _de_names
    del _de
except Exception as _de_e:
    print("[BOOT] I2C_EXT scan: FAILED", repr(_de_e))

oled = init_oled()

# Boot guard part 2: the EARLY sample above (top of file) already decided
# whether to enter debug mode — hold button 2 s at power-on, OR a "debug_mode"
# file on flash. Now that the OLED exists, show the screen and wait:
#   button click → machine.reset()   Ctrl-C → SystemExit → live REPL
if _bg_debug:
    try:
        from src.app.boot_guard import enter_debug
        enter_debug(oled)
    except SystemExit:
        raise
    except Exception as _bg_e:
        print("[BOOT] boot_guard error (ignored):", repr(_bg_e))
    # enter_debug only returns if it couldn't build a button Pin; hand over
    # the REPL anyway rather than continuing a boot the user tried to stop.
    raise SystemExit


# Read config once, early: the WiFi PHY decision below and the boot screen
# (_turtle_boot) need it before step_load_config() runs.
try:
    _early_cfg = load_cfg_dict()
except Exception:
    _early_cfg = None

# Pre-activate the WiFi PHY when WiFi will run, so step_wifi() connects from an
# already-active radio. (On small-heap ESP32s this also kept its rx buffers
# off a fragmented heap; the XIAO's 8 MB PSRAM heap doesn't need that, and its
# screens are imported lazily by run()'s screen cache instead of preloaded.)
if bool((_early_cfg or {}).get("wifi_enabled", False)):
    _net_pre = None
    try:
        import network as _net_pre
        _net_pre.WLAN(_net_pre.STA_IF).active(True)
        print("[BOOT] WiFi PHY pre-activated")
        # NOTE: do NOT connect here: connecting early deadlocks step_wifi()
        # via the WiFi-task mutex.
    except Exception as _pre_e:
        print("[BOOT] WiFi PHY pre-activate failed:", repr(_pre_e))
        # Kill the half-started C WiFi task so it doesn't spam
        # "esp_netif_new_api / nvs alloc out of memory" after boot.
        if _net_pre is not None:
            try:
                _net_pre.deinit()
            except Exception:
                pass
    _net_pre = None
_gc()

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
    if booter and _early_cfg and not _early_cfg.get("turtle_mode"):
        booter.brand = "airOS"
        booter.version = "airOS version " + booter.version_num
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

        # Animate the shared per-step ticker (boot_pipeline created it at
        # zero dots before calling this function) from the connect poll loop.
        def _wifi_tick():
            if booter and booter.step_ticker:
                booter.step_ticker.tick()

        ok, ip, status = wifi.connect(
            cfg.get("wifi_ssid", ""),
            cfg.get("wifi_password", ""),
            # RTCWDT fires at ~15 s — stay under that threshold.
            timeout_s=12,
            retry=0,
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

    # This is the slowest boot step (DNS + connect + server round-trip), so
    # unlike step_rtc it's worth ticking: pass the shared per-step ticker's
    # tick() in so _api_get_ticked() can animate dots while it waits on the
    # socket instead of freezing at zero dots for the whole request.
    _tick_fn = booter.step_ticker.tick if (booter and booter.step_ticker) else None
    ok, detail, info = api_device_lookup(cfg, tick_fn=_tick_fn)
    api_boot = info

    # Persist the mission destination (missions_tb.target_*) into config so
    # nav has a target even before an operator sets a test-project one, and
    # so it survives the next offline boot. Update the in-RAM boot `cfg` too
    # — step_init_runtime() builds the NavController from it.
    try:
        if ok and isinstance(info, dict):
            _md = info.get("mission_destination")
            _updates = {}
            if isinstance(_md, (list, tuple)) and len(_md) == 2:
                _updates["mission_destination"] = [float(_md[0]), float(_md[1])]
            _fn = info.get("mission_dest_full_name")
            if _fn:
                _updates["mission_dest_full_name"] = str(_fn)
            _sn = info.get("mission_dest_short_name")
            if _sn:
                _updates["mission_dest_short_name"] = str(_sn)
            # turtles_tb.name, so an offline boot still has a name to show and
            # to advertise over Bluetooth.
            _dn = str(info.get("device_name") or "").strip()
            if _dn:
                _updates["device_name"] = _dn
            # airOS takes its time zone from the server: air2.earthen.io
            # derives it from the owner's account and an AirBuddy has no
            # phone to set it. A turtle never does (its zone comes from
            # Wrangler's TIMEZONE_SET; hopeturtles.org has no per-turtle zone).
            if cfg is not None and not cfg.get("turtle_mode", True):
                _tz = info.get("tz_offset_min")
                if _tz is not None:
                    _updates["timezone_offset_min"] = int(_tz)
            if _updates and cfg is not None:
                _changed = any(cfg.get(k) != v for k, v in _updates.items())
                if _changed:
                    cfg.update(_updates)
                    from config import update_config
                    update_config(_updates)
                    print("[BOOT] synced from API: destination={} name={!r}".format(
                        _updates.get("mission_destination"), _updates.get("device_name")))
    except Exception as e:
        print("[BOOT] mission destination sync failed:", repr(e))

    # A set_* change that never reached the dashboard (set offline, or the
    # PATCH failed): push it now while the server just answered (B13). The
    # background thread keeps retrying if this fails too.
    if ok:
        try:
            from src.net import device_client
            _pushed = device_client.push_pending(cfg)
            if _pushed is not None:
                print("[BOOT] pending dashboard set_* fields:",
                      "pushed" if _pushed else "PATCH failed, background will retry")
        except Exception as e:
            print("[BOOT] dashboard push failed:", repr(e))

    try:
        from src.ui import connection_header
        connection_header.set_api_ok(bool(ok))
    except Exception:
        pass

    return True, ("OK" if ok else detail)


def step_rtc():
    global rtc_info
    # The DS3231 read is a single fast I2C call with no loop to tick the
    # shared ticker from, so this stays at the zero dots boot_pipeline
    # already drew for this step. (The rare NTP fallback below is a single
    # blocking call too, but it's the exception path, not the common case
    # step_api's ticking was added for.)
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
                info["source"] = "ntp"
                _ntp_src = " via NTP"
                print("[RTC] NTP sync OK")
            else:
                print("[RTC] NTP failed:", ntp.get("error") or ntp.get("reason"))
        except Exception as _ne:
            print("[RTC] NTP error:", repr(_ne))

    # NOT FOUND only if DS3231 absent and NTP also failed
    if not info.get("detected") and not info.get("synced"):
        return True, "RTC not found"

    if not info.get("synced"):
        _r = info.get("reason") or info.get("error") or "?"
        return True, "FOUND – sync fail ({}){}".format(_r, _bat)

    # Synced — fold the time into the result line (keeps output to two lines).
    dt = info.get("dt_utc")
    if not dt or len(dt) < 7:
        return True, "OK{}".format(_ntp_src)

    h, mi = dt[4], dt[5]

    # The turtle's own time zone (config, set from the phone), never the
    # server's, so this line agrees with the Time screen.
    tz_min = None
    if cfg:
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
            return True, "Its {:02d}:{:02d}".format(lh, lmi)
        except Exception:
            pass

    return True, "Its {:02d}:{:02d}".format(h, mi)


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
    # BME280: 0x76 (SDO=LOW) or 0x77 (SDO=HIGH — also used by BMP280 at alt addr).
    # 0x77 is ALSO the GY-87's BMP180 barometer (owned by step_imu). Only hand
    # 0x77 to AirSensor when the chip-ID register says BME280 (0x60) / BMP280 (0x58).
    _bme_addr = None
    if I2C_ADDR_BME280_ALT in addrs and _chip_id(I2C_ADDR_BME280_ALT) in (0x60, 0x58):
        _bme_addr = I2C_ADDR_BME280_ALT
    elif I2C_ADDR_BME280 in addrs:
        _bme_addr = I2C_ADDR_BME280
    has_bme   = _bme_addr is not None

    if not (has_aht or has_ens or has_scd41 or has_bme):
        return True, "No sensors found"

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
        return True, "AS5600 not found"
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


# Built once here, shared by NavController (HeadingSource), the compass screen
# and the telemetry scheduler via run(imu=...). None when no GY-87 is present.
_rt_imu = None


def step_imu():
    """Probe the GY-87 10DOF board on I2C_EXT: MPU6050 (0x68 factory, or
    0x69 with AD0 high), its aux-bus magnetometer (QMC5883L 0x0D / HMC5883L
    0x1E / QMC5883P 0x2C, visible after bypass) and BMP180 (0x77). The GY87 instance is kept
    in _rt_imu so nothing else re-probes the bus.

    The IMU is never looked for on I2C_SYS: 0x68 there is the DS3231. A
    standalone GY-271 compass on I2C_SYS still boots green — HeadingSource
    finds it itself (src/nav/heading.py)."""
    global _rt_imu

    ext = i2c_scan_ext()
    imu_addr = None
    if I2C_ADDR_MPU6050 in ext:
        imu_addr = I2C_ADDR_MPU6050
    elif I2C_ADDR_MPU6050_ALT in ext:
        imu_addr = I2C_ADDR_MPU6050_ALT

    if imu_addr is not None or I2C_ADDR_BMP180 in ext:
        try:
            _gc()
            from src.hal.board import init_i2c_ext
            from src.drivers.gy87 import GY87
            dev = GY87(init_i2c_ext(), imu_addr=(imu_addr or I2C_ADDR_MPU6050))
            if dev.is_present or dev.baro is not None:
                _rt_imu = dev
                # A GY-87 without its mag is a failed compass, even if the
                # IMU or barometer answered.
                if dev.mag is None:
                    return False, "Compass FAIL - " + dev.summary() + " only"
                return True, "OK - " + dev.summary()
        except Exception as e:
            print("[IMU] GY87 probe failed:", repr(e))
    else:
        print("[IMU] no GY-87 on I2C_EXT (SDA=GPIO8 SCL=GPIO3)")

    # Standalone compass on I2C_SYS (legacy GY-271 wiring).
    addrs = i2c_scan()
    try:
        _gc()
        from src.drivers.hmc5883l_qmc5883l import QMC5883L, HMC5883L, QMC5883P
        i2c = init_i2c()
        if I2C_ADDR_QMC5883 in addrs:
            m = QMC5883L(i2c)
            if m.is_present:
                return True, "QMC5883L standalone (0x0D)"
        if I2C_ADDR_HMC5883 in addrs:
            m = HMC5883L(i2c)
            if m.is_present:
                return True, "HMC5883L standalone (0x1E)"
        if I2C_ADDR_QMC5883P in addrs:
            m = QMC5883P(i2c)
            if m.is_present:
                return True, "QMC5883P standalone (0x2C)"
    except Exception:
        pass

    return False, "Compass NOT FOUND"


_rt_i2c = None
_rt_ina = None
_rt_gps = None
_rt_wifi_mgr = None
_rt_nav = None
_rt_turtle_scr = None
_rt_ble = None


def _rt_mission_name():
    # Mirrors src/app/main.py's _mission_name() — kept here too since
    # TurtleWaitingScreen is now built during boot, before run() exists.
    try:
        if isinstance(api_boot, dict):
            sn = api_boot.get("mission_short_name") or api_boot.get("mission_full_name")
            if sn:
                return sn
    except Exception:
        pass
    return None


def step_init_runtime():
    """
    Build the runtime objects (I2C, battery monitor, GPS session, WiFi
    manager, nav controller, turtle waiting screen) that src.app.main.run()
    used to construct silently AFTER the boot screen had already gone
    blank — leaving several seconds of dead screen between "Locked &
    loaded!" and the first drawn frame. Building them here keeps that work
    visible under the boot logo/progress bar instead. run() uses whatever
    was built here and only falls back to building it itself if a piece is
    missing (something here failed).
    """
    global _rt_i2c, _rt_ina, _rt_gps, _rt_wifi_mgr, _rt_nav, _rt_turtle_scr, _rt_ble

    if not cfg:
        return True, "SKIPPED (No config)"

    _gc()

    try:
        _rt_i2c = init_i2c()
    except Exception as e:
        print("[NAV] i2c init failed:", repr(e))
        _rt_i2c = None

    try:
        from src.drivers.ina219 import INA219
        INA219.BATT_R_OHM = float(cfg.get("battery_r_ohm", INA219.BATT_R_OHM))
        _ina = INA219(_rt_i2c, auto_init=True)
        _rt_ina = _ina if _ina.is_present else None
    except Exception:
        _rt_ina = None

    if cfg.get("gps_enabled", False):
        try:
            from src.sensors.xiao_gnss import GnssModule
            _rt_gps = GnssModule(uart_id=GPS_UART_ID, baud=GPS_BAUD,
                                  tx_pin=GPS_TX_PIN, rx_pin=GPS_RX_PIN)
            _rt_gps.configure_mode(cfg.get("turtle_mode", False),
                                    module=cfg.get("gps_module", "l76k"))
        except Exception as e:
            print("[NAV] gps init failed:", repr(e))
            _rt_gps = None

    # Only re-enter the WiFi driver here if boot's own connection attempt
    # actually succeeded, to avoid RTCWDT risk from broken driver state.
    _boot_wifi_ok = isinstance(wifi_boot, dict) and wifi_boot.get("ok")
    if cfg.get("wifi_enabled", False) and _boot_wifi_ok:
        try:
            from src.net.net_caps import wifi_supported
            if wifi_supported():
                from src.net.wifi_manager import WiFiManager
                _rt_wifi_mgr = WiFiManager()
        except Exception as e:
            print("[NAV] wifi manager init failed:", repr(e))
            _rt_wifi_mgr = None

    if not cfg.get("turtle_mode", False):
        _gc()
        return True, "OK"

    # Turtle Wrangler BLE peripheral (docs/wrangler/). Built once; the radio
    # only comes up when ble_enabled. run() ticks it from _bg_tick.
    try:
        from src.net import ble_service
        _rt_ble = ble_service.init(cfg)
    except Exception as e:
        print("[BLE] init failed:", repr(e))
        _rt_ble = None

    nav_servo = None
    if cfg.get("servo_present", False):
        try:
            from src.hal.board import servo_pin as _servo_pin
            from src.drivers.servo import Servo
            _sp = _servo_pin()
            if _sp is not None:
                nav_servo = Servo(
                    _sp, min_us=int(cfg.get("servo_min_us", 500)),
                    max_us=int(cfg.get("servo_max_us", 2500)))
        except Exception as e:
            print("[NAV] servo init failed:", repr(e))

    try:
        from src.nav.controller import NavController
        _rt_nav = NavController(cfg, i2c=_rt_i2c, gps=_rt_gps,
                                 servo=nav_servo, battery=_rt_ina,
                                 imu=_rt_imu)
    except Exception as e:
        print("[NAV] controller init failed:", repr(e))
        _rt_nav = None

    if oled is not None:
        try:
            from src.ui.screens.turtle_waiting import TurtleWaitingScreen
            _rt_turtle_scr = TurtleWaitingScreen(
                oled,
                nav_get=lambda: _rt_nav,
                mission_get=_rt_mission_name,
                battery_get=lambda: (_rt_ina.battery_v() if _rt_ina else None),
                current_get=lambda: (_rt_ina.current_ma() if _rt_ina else None),
            )
        except Exception as e:
            print("[NAV] turtle_waiting init failed:", repr(e))
            _rt_turtle_scr = None

    _gc()
    return True, ("OK" if _rt_nav else "NAV FAIL")


# Keep the Pin object alive in a module-level global so GC cannot finalize it
# between boot steps.  On some MicroPython/ESP32-S3 builds, Pin finalization
# resets the GPIO to input mode, immediately extinguishing the LED.
_boot_led_gpio = None   # pin number (int) — used for cleanup
_boot_led_active = 1    # logic level that means "on"
_boot_led_pin = None    # Pin object kept alive to hold the GPIO HIGH/LOW state
_boot_led_timer = None  # background timer that blinks the LED during boot
_boot_led_on = False    # current blink phase (True = LED lit)


def _boot_led_blink(_t=None):
    """Timer callback: toggle the onboard LED to show the device is alive."""
    global _boot_led_on
    try:
        if _boot_led_pin is None:
            return
        _boot_led_on = not _boot_led_on
        _boot_led_pin.value(_boot_led_active if _boot_led_on else (1 - _boot_led_active))
    except Exception:
        pass


def step_led():
    """
    Start blinking the onboard user LED for the remainder of boot to show the
    device is alive and booting.  A background machine.Timer toggles the LED
    every 0.5 s so the blink is steady regardless of how long individual boot
    steps take (WiFi connect can block for several seconds).
    Stopped and turned off by the cleanup block that follows boot_pipeline().
    """
    global _boot_led_gpio, _boot_led_active, _boot_led_pin, _boot_led_timer, _boot_led_on
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
        _boot_led_on = True
        _boot_led_pin.value(active_val)  # start lit, then blink from here
        # 0.5 s periodic blink in the background (independent of boot-step timing).
        # Hardware timer id from src/hal/timers.py (BOOT_LED = 0, deinit'd
        # before boot ends). Timer(-1) raises on some ESP32-S3 builds.
        from src.hal import timers as _timers
        _boot_led_timer = _timers.periodic(_timers.BOOT_LED, 500, _boot_led_blink)
        print("[BOOT] LED: OK GPIO{} (active={}), blinking @0.5s".format(led_pin, active_val))
        return True, "LED OK (GPIO{})".format(led_pin)
    except Exception as e:
        print("[BOOT] LED: failed:", repr(e))
        return True, "LED FAIL"


# Ordering notes:
#   - LED first: start the "alive" blink up front so it runs through the whole
#     boot, including the long WiFi connect wait.  step_led only touches the
#     onboard GPIO/Timer (no config or I2C dependency), so it is safe to run first.
#   - WiFi early (ESP32 heap stability), API right after WiFi.
_turtle_boot = bool((_early_cfg or {}).get("turtle_mode", False))

# Labels intentionally carry no trailing "...": boot_pipeline gives every
# step its own DotTicker (starts at zero dots) that ticks only if the step's
# fn() has a poll loop to drive it. A hardcoded "..." would misrepresent a
# step that finished before a single dot could show.
steps = [
    ("LED check", step_led),
    ("Loading config", step_load_config),
    ("WiFi connect", step_wifi),
    ("Device API check", step_api),
    ("RTC clock", step_rtc, True, 1500),  # hold the synced time on screen 1.5 s
    ("Warming sensors", step_warmup),
    ("GPS check", step_gps),
    ("10DOF IMU", step_imu),  # GY-87: MPU6050 + aux-bus mag (bypass) + BMP180; instance kept in _rt_imu
]
if _turtle_boot:
    steps += [
        ("AS5600 sailpoint", step_as5600),  # imports AS5600 driver if found on I2C
    ]

# Last step: build the runtime objects run() needs (I2C, battery, GPS
# session, WiFi manager, nav controller, turtle waiting screen) here, under
# the boot bar, instead of leaving them to run() to build silently behind a
# blank screen. Label reflects what's actually happening in each mode.
# show_footer=False: its label is shown again — centered, full-screen — by
# boot_pipeline's finishing transition right after this step completes, so
# the under-logo footer is suppressed to avoid printing it twice.
steps += [
    ("Initiating nav" if _turtle_boot else "Finishing boot", step_init_runtime, False),
]

if booter:
    try:
        booter.boot_pipeline(
            steps,
            intro_ms=500,
            fps=18,
            settle_ms=BOOT_STEP_HOLD_MS,  # <-- hold each step on OLED
            logger=_log,
            # "\n" -> stacked centered lines; dots animate on the last line.
            finishing_label=("Initiating\nNav" if _turtle_boot else "Finishing boot"),
        )
    except Exception as e:
        print("BOOTER error:", repr(e))
else:
    for _item in steps:
        label, fn = _item[0], _item[1]  # steps may be 2-, 3- or 4-tuples
        _log("[BOOT] " + label)
        try:
            ok, detail = fn()
            _log("[BOOT] {} -> {}".format(label, detail))
        except Exception as e:
            _log("[BOOT] {} -> ERROR {}".format(label, repr(e)))
        _sleep_hold()  # <-- hold each step even without OLED


# Stop the blink timer and turn off the boot LED now that all steps are done.
if _boot_led_timer is not None:
    try:
        _boot_led_timer.deinit()
    except Exception:
        pass
    _boot_led_timer = None
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

# WiFi connected but the device API rejected the id/key pair — surface this
# instead of leaving the user staring at a waiting screen that just never
# shows a connection, with no clue why (e.g. a mistyped device_id).
if (
    oled is not None
    and isinstance(wifi_boot, dict) and wifi_boot.get("ok")
    and isinstance(api_boot, dict) and not api_boot.get("ok")
    and api_boot.get("http_status") in (401, 403)
):
    try:
        from src.ui.screens.frowny import FrownyScreen
        FrownyScreen(oled).show(None, line1="No connection:", line2="key mismatch!")
        time.sleep_ms(3000)
        go_waiting(oled, wifi_boot=wifi_boot, api_boot=api_boot, gps_boot=gps_boot)
    except Exception as e:
        print("KEY MISMATCH screen error:", repr(e))


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
    # Tighten the watchdog from the boot timeout to the running one, and log
    # why the chip last reset (the RTC is synced by now).
    _wd_on = False
    if _watchdog is not None:
        try:
            _watchdog.note_boot()
            if _watchdog.enabled():
                _wd_on = _watchdog.start(_watchdog.timeout_s(cfg))
        except Exception as _wd_e:
            print("[BOOT] watchdog arm error:", repr(_wd_e))
    try:
        from src.app.main import run
        run(
            rtc_synced=bool(rtc_info and rtc_info.get("synced")),
            wifi_boot=wifi_boot,
            api_boot=api_boot,
            oled=oled,
            air_sensor=air,
            boot_warmup_started=True,
            rtc_info=rtc_info,
            i2c=_rt_i2c,
            ina_dev=_rt_ina,
            gps=_rt_gps,
            wifi_manager=_rt_wifi_mgr,
            nav_controller=_rt_nav,
            turtle_waiting_scr=_rt_turtle_scr,
            imu=_rt_imu,
            ble=_rt_ble,
        )
    except KeyboardInterrupt:
        # Ctrl-C: the watchdog can't be stopped, so stretch it to 24 h or it
        # would reset the board out from under the REPL.
        if _watchdog is not None:
            _watchdog.relax()
        raise
    except Exception as e:
        print("turtleOS crash:", repr(e))
        if _watchdog is not None and _watchdog.enabled():
            # Logs the traceback to crash.txt, then resets (3 s; 10 min from
            # the 4th crash in a row). Does not return.
            _watchdog.handle_crash(e)
        # watchdog_enabled false (bench): log it and stay at the REPL.
        try:
            if _watchdog is not None:
                _watchdog.record_crash(e)
        except Exception:
            pass
        raise
else:
    # HAL is broken — show message for 30 s then auto-reset so a redeploy takes effect
    import machine as _machine
    _deadline = time.ticks_add(time.ticks_ms(), 30_000)
    while time.ticks_diff(_deadline, time.ticks_ms()) > 0:
        time.sleep(5)
    _machine.reset()