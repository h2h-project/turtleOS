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

        self._top_pad = 5

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
    def _draw(self, wifi_flash=None, api_flash=None):
        """
        wifi_flash : when not None, forces WiFi icon on(True)/off(False) for animation.
        api_flash  : when not None, forces API icon on(True)/off(False) for animation.
        Both default to None which means live-probe / cache as normal.
        """
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
                    api_connected=api_flash,
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

        o.f_med.write(self._last_status[:18], 0, data_y)

        if connected:
            if self.ssid:
                o.f_med.write(self.ssid[:18], 0, data_y + line_h)
            if self._last_ip:
                o.f_med.write(self._last_ip[:18], 0, data_y + line_h * 2)

        self.toggle.draw(fb, on=connected)
        fb.show()

    # ----------------------------
    # Animated WiFi connect (min 1.5 s of visible flashing)
    # ----------------------------
    def _animated_connect(self, min_ms=1500, timeout_ms=10000):
        """
        Attempt WiFi connection with WiFi icon flashing every 300 ms.
        Flashes for at least min_ms even if connect is instant or already done.
        Returns (ok: bool, ip: str).
        """
        # Check if already up
        already = self._is_connected()

        if not already and self.ssid:
            try:
                self.wifi.active(True)
            except Exception:
                pass
            # Use the underlying wlan object for non-blocking connect
            wlan = getattr(self.wifi, "wlan", None)
            if wlan:
                try:
                    wlan.connect(self.ssid, self.password)
                except Exception:
                    already = True   # no wlan → fall back to polling _is_connected
            else:
                already = True

        start_ms = time.ticks_ms()
        # deadline must cover at least min_ms; also allow up to timeout_ms for real connect
        deadline_ms = time.ticks_add(start_ms, max(int(timeout_ms), int(min_ms) + 500))
        min_end_ms  = time.ticks_add(start_ms, int(min_ms))

        # Start with icon visible, first flip after 300 ms
        flash_on = True
        last_flash_ms = time.ticks_add(start_ms, -300)
        self._last_status = "Checking..."
        found = False
        result = (False, "")

        while True:
            now = time.ticks_ms()
            past_min = time.ticks_diff(now, min_end_ms) >= 0

            # Flash every 300 ms (keep flashing until min_ms even after found)
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
                        except Exception:
                            pass
                    else:
                        try:
                            connected = wlan.isconnected()
                            if connected:
                                ip = wlan.ifconfig()[0]
                        except Exception:
                            pass
                else:
                    connected = self._is_connected()
                    if connected:
                        ip = self.wifi.ip() or ""

                if connected:
                    found = True
                    result = (True, ip)
                    self._last_ip = ip
                    self._last_status = "Connected"
                elif time.ticks_diff(now, deadline_ms) >= 0:
                    found = True
                    result = (False, "")
                    self._last_status = "Not connected"
                    self._last_ip = ""

            if found and past_min:
                # Settle on solid icon for the final state before returning
                self._draw(wifi_flash=result[0])
                time.sleep_ms(80)
                return result

            time.sleep_ms(50)

    # ----------------------------
    # Animated API check (after WiFi connects)
    # ----------------------------
    def _animated_api_check(self, min_ms=1500):
        """
        Flash the API icon every 300 ms while pinging the API.
        Runs for at least min_ms total. Updates the connection_header cache.
        Leaves the API icon solid if the ping succeeded.
        """
        cfg = self.cfg or {}
        api_base = (cfg.get("api_base") or "").strip().rstrip("/")
        device_id = (cfg.get("device_id") or "").strip()
        device_key = (cfg.get("device_key") or "").strip()

        start_ms = time.ticks_ms()
        min_end_ms = time.ticks_add(start_ms, int(min_ms))
        flash_on = False
        last_flash_ms = time.ticks_add(start_ms, -300)  # first flip is immediate

        self._draw(api_flash=False)

        # --- Blocking HTTP ping ---
        ok = False
        if api_base and device_id and device_key:
            try:
                import gc as _gc
                _gc.collect()
                import urequests as _req
                r = _req.get(
                    api_base + "/api/v1/device?compact=1",
                    headers={"X-Device-Id": device_id, "X-Device-Key": device_key},
                )
                code = getattr(r, "status_code", None)
                try:
                    r.close()
                except Exception:
                    pass
                _gc.collect()
                ok = code is not None and 200 <= int(code) < 300
            except Exception:
                ok = False

        # Update the module-level API cache
        if _ch:
            try:
                _ch.set_api_ok(ok)
            except Exception:
                pass

        self._last_status = "Connected" if self._is_connected() else "Not connected"

        # --- Flash until min_ms has elapsed ---
        flash_on = ok   # start from the result state
        while True:
            now = time.ticks_ms()
            past_min = time.ticks_diff(now, min_end_ms) >= 0

            if time.ticks_diff(now, last_flash_ms) >= 300:
                flash_on = not flash_on
                last_flash_ms = now
                self._draw(api_flash=flash_on)

            if past_min:
                # Settle: solid if connected, hollow if not
                self._draw(api_flash=ok if ok else None)
                time.sleep_ms(80)
                return ok

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

        # Hollow out API icon — it will be checked on the Online screen
        if _ch:
            try:
                _ch.set_api_ok(False)
            except Exception:
                pass

        self._reload_cfg()

        if self.enabled:
            ok, _ = self._animated_connect(min_ms=1500, timeout_ms=10000)
            if ok:
                self._animated_api_check(min_ms=1500)
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

            elif action == "double":
                self.enabled = not self.enabled
                self.cfg["wifi_enabled"] = self.enabled
                save_config(self.cfg)

                if self.enabled:
                    ok, _ = self._animated_connect(min_ms=1500, timeout_ms=10000)
                    if ok:
                        self._animated_api_check(min_ms=1500)
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
