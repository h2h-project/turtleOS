# src/ui/screens/wifi.py  (MicroPython / Pico-safe)

import time
import sys as _sys
from src.ui.toggle import ToggleSwitch
from config import load_config, save_config
from src.net.wifi_manager import WiFiManager

try:
    from src.ui import connection_header as _ch
    from src.ui.connection_header import GPS_NONE
except Exception:
    _ch = None
    GPS_NONE = 0

_IS_ESP32 = getattr(_sys, "platform", "") == "esp32"


class WiFiScreen:
    def __init__(self, oled):
        self.oled = oled
        self.wifi = WiFiManager()

        self._top_pad = 0

        w = int(getattr(oled, "width", 128))
        h = int(getattr(oled, "height", 64))

        tx = 100
        ty = 16 + self._top_pad
        tw = 24
        th = 40

        if tx < 0:
            tx = 0
        if ty < 0:
            ty = 0
        if tx + tw > w:
            tw = max(1, w - tx)
        if ty + th > h:
            th = max(1, h - ty)

        self.toggle = ToggleSwitch(x=tx, y=ty, w=tw, h=th)

        self.cfg = {}
        self.enabled = False
        self.ssid = ""
        self.password = ""

        self._last_status = ""
        self._last_ip = ""
        self._last_refresh_ms = 0

    # ----------------------------
    # Config
    # ----------------------------
    def _reload_cfg(self):
        self.cfg = load_config()
        self.enabled = bool(self.cfg.get("wifi_enabled", False))
        self.ssid = self.cfg.get("wifi_ssid", "") or ""
        self.password = self.cfg.get("wifi_password", "") or ""

    # ----------------------------
    # Connection helpers
    # ----------------------------
    def _is_connected(self):
        try:
            return bool(self.wifi.is_connected())
        except Exception:
            return False

    def _live_update(self):
        if self._is_connected():
            try:
                self._last_ip = self.wifi.ip() or ""
            except Exception:
                self._last_ip = ""
            self._last_status = "Connected"
        else:
            self._last_status = "Not connected"
            self._last_ip = ""

    # ----------------------------
    # Drawing
    # ----------------------------
    def _draw(self, wifi_flash=None):
        o = self.oled
        fb = o.oled
        fb.fill(0)

        if _ch:
            try:
                _ch.draw(
                    fb,
                    o.width,
                    gps_state=_ch.get_gps_state(),
                    wifi_override=wifi_flash,
                    api_connected=None,
                    api_sending=False,
                    icon_y=1,
                )
            except Exception:
                pass

        title_y = self._top_pad
        o.f_arvo20.write("WiFi", 0, title_y)

        try:
            _, title_h = o._text_size(o.f_arvo20, "Ag")
        except Exception:
            title_h = 20

        data_y = int(title_y + title_h + 4)
        line_h = 13

        connected = (self._last_status == "Connected") and self._is_connected()

        if not self.enabled:
            o.f_med.write("WiFi is set off.", 0, data_y)
            o.f_med.write("2x click to", 0, data_y + line_h)
            o.f_med.write("turn on.", 0, data_y + line_h * 2)
        else:
            o.f_med.write(self._last_status[:18], 0, data_y)
            if connected:
                if self.ssid:
                    o.f_med.write(self.ssid[:18], 0, data_y + line_h)
                if self._last_ip:
                    o.f_med.write(self._last_ip[:18], 0, data_y + line_h * 2)

        self.toggle.draw(fb, on=self.enabled)
        fb.show()

    # ----------------------------
    # Animated WiFi connect with detailed logging
    # ----------------------------
    def _animated_connect(self, min_ms=1500, timeout_ms=10000):
        """
        Attempt WiFi connection with WiFi icon flashing every 300 ms.
        Flashes for at least min_ms even if connect is instant or already done.
        Returns (ok: bool, ip: str).
        """
        print("[WIFI_SCR] --- connect start ---")
        try:
            import gc as _gc_w
            print("[WIFI_SCR] heap: %d KB free" % (_gc_w.mem_free() // 1024))
        except Exception:
            pass
        print("[WIFI_SCR] ssid=%r  timeout_ms=%d" % (self.ssid, timeout_ms))

        already = self._is_connected()
        print("[WIFI_SCR] already_connected=%s" % already)

        if not already and self.ssid:
            try:
                self.wifi.active(True)
                print("[WIFI_SCR] radio active(True) ok")
            except Exception as _e:
                print("[WIFI_SCR] active(True) err:", repr(_e))

            wlan = getattr(self.wifi, "wlan", None)
            if wlan:
                try:
                    # Log radio state before calling connect
                    try:
                        _st = wlan.status()
                        print("[WIFI_SCR] pre-connect status=%s" % _st)
                    except Exception:
                        print("[WIFI_SCR] pre-connect status=<unavailable>")
                    wlan.connect(self.ssid, self.password)
                    print("[WIFI_SCR] wlan.connect() issued")
                except Exception as _e:
                    print("[WIFI_SCR] wlan.connect() FAILED:", repr(_e))
                    already = True   # fall through to polling _is_connected
            else:
                print("[WIFI_SCR] no wlan obj — skip raw connect, will poll")
                already = True
        elif already:
            print("[WIFI_SCR] already up, skip connect call")
        else:
            print("[WIFI_SCR] no SSID configured — cannot connect")

        start_ms = time.ticks_ms()
        deadline_ms = time.ticks_add(start_ms, max(int(timeout_ms), int(min_ms) + 500))
        min_end_ms  = time.ticks_add(start_ms, int(min_ms))

        flash_on = True
        last_flash_ms = time.ticks_add(start_ms, -300)
        last_log_ms   = time.ticks_add(start_ms, -1000)   # first poll log fires immediately
        self._last_status = "Checking..."
        found = False
        result = (False, "")

        print("[WIFI_SCR] polling (interval=1s, timeout=%ds)..." % (timeout_ms // 1000))

        while True:
            now = time.ticks_ms()
            elapsed = time.ticks_diff(now, start_ms)
            past_min = time.ticks_diff(now, min_end_ms) >= 0

            # Flash every 300 ms
            if not past_min or not found:
                if time.ticks_diff(now, last_flash_ms) >= 300:
                    flash_on = not flash_on
                    last_flash_ms = now
                    self._draw(wifi_flash=flash_on)

            if not found:
                ip = ""
                connected = False

                wlan = getattr(self.wifi, "wlan", None)
                if wlan and not already:
                    if _IS_ESP32:
                        try:
                            ip = wlan.ifconfig()[0]
                            connected = bool(ip and ip != "0.0.0.0")
                        except Exception as _e:
                            if time.ticks_diff(now, last_log_ms) >= 1000:
                                print("[WIFI_SCR] ifconfig() err:", repr(_e))
                    else:
                        try:
                            connected = wlan.isconnected()
                            if connected:
                                ip = wlan.ifconfig()[0]
                        except Exception as _e:
                            if time.ticks_diff(now, last_log_ms) >= 1000:
                                print("[WIFI_SCR] isconnected() err:", repr(_e))
                else:
                    connected = self._is_connected()
                    if connected:
                        ip = self.wifi.ip() or ""

                # Log poll state every 1 s
                if time.ticks_diff(now, last_log_ms) >= 1000:
                    last_log_ms = now
                    if _IS_ESP32 and wlan and not already:
                        # status() can deadlock during association on ESP32 —
                        # log ifconfig instead which is safe via the LWIP lock
                        try:
                            _ifc = wlan.ifconfig()[0]
                            print("[WIFI_SCR] t=%dms ip=%r connected=%s" % (elapsed, _ifc, connected))
                        except Exception:
                            print("[WIFI_SCR] t=%dms connected=%s (ifconfig err)" % (elapsed, connected))
                    else:
                        try:
                            _st = wlan.status() if wlan else "?"
                        except Exception:
                            _st = "<err>"
                        print("[WIFI_SCR] t=%dms status=%s connected=%s" % (elapsed, _st, connected))

                if connected:
                    found = True
                    result = (True, ip)
                    self._last_ip = ip
                    self._last_status = "Connected"
                    print("[WIFI_SCR] CONNECTED ip=%r at t=%dms" % (ip, elapsed))
                elif time.ticks_diff(now, deadline_ms) >= 0:
                    found = True
                    result = (False, "")
                    self._last_status = "Not connected"
                    self._last_ip = ""
                    # Final status snapshot on timeout
                    try:
                        if _IS_ESP32 and wlan:
                            _fin_ip = wlan.ifconfig()[0]
                            print("[WIFI_SCR] TIMEOUT at t=%dms final_ip=%r" % (elapsed, _fin_ip))
                        elif wlan:
                            _fin_st = wlan.status()
                            print("[WIFI_SCR] TIMEOUT at t=%dms final_status=%s" % (elapsed, _fin_st))
                        else:
                            print("[WIFI_SCR] TIMEOUT at t=%dms (no wlan obj)" % elapsed)
                    except Exception as _e:
                        print("[WIFI_SCR] TIMEOUT at t=%dms (status err: %s)" % (elapsed, repr(_e)))

            if found and past_min:
                self._draw(wifi_flash=result[0])
                time.sleep_ms(80)
                print("[WIFI_SCR] --- connect done ok=%s ---" % result[0])
                return result

            time.sleep_ms(50)

    # ----------------------------
    # Public Entry
    # ----------------------------
    def show_live(self, btn, tick_fn=None):
        """
        Single click  : advance carousel (return "single")
        Double click  : toggle WiFi enabled
        tick_fn       : background callable, fired every 500 ms
        """
        try:
            btn.reset()
        except Exception:
            pass

        # Hollow out API icon — it will be refreshed by the Online screen
        if _ch:
            try:
                _ch.set_api_ok(False)
            except Exception:
                pass

        self._reload_cfg()

        if self.enabled:
            self._animated_connect(min_ms=1500, timeout_ms=10000)
        else:
            try:
                self.wifi.disconnect()
            except Exception:
                pass
            try:
                self.wifi.active(False)
            except Exception:
                pass
            self._last_status = "WiFi disabled"
            self._last_ip = ""
            self._draw()

        self._last_refresh_ms = time.ticks_ms()
        _tick_next = time.ticks_ms()
        _tick_every = 500

        while True:
            now = time.ticks_ms()

            if tick_fn is not None and time.ticks_diff(now, _tick_next) >= 0:
                try:
                    tick_fn()
                except Exception:
                    pass
                _tick_next = time.ticks_add(now, _tick_every)

            # Periodic refresh
            if time.ticks_diff(now, self._last_refresh_ms) > 500:
                self._last_refresh_ms = now
                self._live_update()
                self._draw()

            try:
                action = btn.poll_action()
            except Exception:
                action = None

            if action == "single":
                return "single"

            elif action == "quad":
                return "quad"

            elif action == "sleep":
                return "sleep"

            elif action == "double":
                self.enabled = not self.enabled
                self.cfg["wifi_enabled"] = self.enabled
                save_config(self.cfg)

                if self.enabled:
                    self._animated_connect(min_ms=1500, timeout_ms=10000)
                else:
                    try:
                        self.wifi.disconnect()
                    except Exception:
                        pass
                    try:
                        self.wifi.active(False)
                    except Exception:
                        pass
                    self._last_status = "WiFi disabled"
                    self._last_ip = ""
                    self._draw()

            time.sleep_ms(25)
