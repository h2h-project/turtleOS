# src/ui/screens/online.py  (MicroPython / Pico-safe)

import time
import gc

from config import load_config
from src.ui.toggle import ToggleSwitch

try:
    from src.ui import connection_header as _ch
    from src.ui.connection_header import GPS_NONE
except Exception:
    _ch = None
    GPS_NONE = 0


class OnlineScreen:
    def __init__(self, oled):
        self.oled = oled
        self._top_pad = 5

        w = int(getattr(oled, "width", 128))
        h = int(getattr(oled, "height", 64))

        tx = 100
        ty = 16 + self._top_pad
        tw = 24
        th = 43
        if tx + tw > w:
            tw = max(1, w - tx)
        if ty + th > h:
            th = max(1, h - ty)

        self.toggle = ToggleSwitch(x=tx, y=ty, w=tw, h=th)

        self._status = "Connecting..."
        self._connected = False
        self._connecting = False
        self._api_flash_on = False
        self._wifi_ok = False
        self._gps_state = GPS_NONE

        self._load_cfg()

    # ----------------------------------------
    # Config
    # ----------------------------------------

    def _load_cfg(self):
        self.cfg = load_config()
        self.api_base = (self.cfg.get("api_base") or "http://air2.earthen.io").strip().rstrip("/")
        self.device_id = self.cfg.get("device_id", "")
        self.device_key = self.cfg.get("device_key", "")

    # ----------------------------------------
    # Drawing
    # ----------------------------------------

    def _draw(self):
        o = self.oled
        if o is None:
            return
        fb = o.oled
        fb.fill(0)

        if _ch:
            try:
                api_icon = self._api_flash_on if self._connecting else self._connected
                _ch.draw(
                    fb,
                    o.width,
                    gps_state=self._gps_state,
                    api_connected=api_icon,
                    wifi_override=self._wifi_ok,
                    api_sending=False,
                    icon_y=1,
                )
            except Exception:
                pass

        o.f_arvo20.write("Online", 0, self._top_pad)

        try:
            _, title_h = o._text_size(o.f_arvo20, "Ag")
        except Exception:
            title_h = 20

        line_h = 13
        status_y = self._top_pad + title_h + 2

        o.f_med.write(self._status[:18], 0, status_y)

        did = (self.device_id or "---")[:14]
        o.f_med.write("ID: " + did, 0, status_y + line_h)

        key = self.device_key or ""
        if len(key) > 5:
            key_disp = key[:5] + "*" * min(5, len(key) - 5)
        elif key:
            key_disp = key
        else:
            key_disp = "---"
        o.f_med.write("Key: " + key_disp, 0, status_y + line_h * 2)

        self.toggle.draw(fb, on=self._connected)
        fb.show()

    # ----------------------------------------
    # Connection attempt
    # ----------------------------------------

    def _connect(self, min_ms=500):
        """
        Show 'Connecting...' + flash API icon for at least min_ms, then do a
        GET /api/v1/device?compact=1 — the same request the DeviceScreen uses,
        which is known to work.  Toggle turns ON only on a 2xx response.
        """
        deadline_ms = time.ticks_add(time.ticks_ms(), int(min_ms))

        self._connecting = True
        self._connected = False
        self._api_flash_on = False
        self._status = "Connecting..."
        self._draw()

        # Flash API icon while waiting out the minimum visible time
        last_flash_ms = time.ticks_add(time.ticks_ms(), -300)  # fires on first iteration
        while time.ticks_diff(time.ticks_ms(), deadline_ms) < 0:
            now = time.ticks_ms()
            if time.ticks_diff(now, last_flash_ms) >= 250:
                self._api_flash_on = not self._api_flash_on
                last_flash_ms = now
                self._draw()
            time.sleep_ms(50)

        # --- Blocking GET to /api/v1/device (same endpoint DeviceScreen uses) ---
        ok = False
        r = None
        try:
            gc.collect()
            import urequests as _req
            url = self.api_base + "/api/v1/device?compact=1"
            headers = {
                "X-Device-Id": self.device_id,
                "X-Device-Key": self.device_key,
            }
            r = _req.get(url, headers=headers)
            code = getattr(r, "status_code", None)
            ok = (code is not None and 200 <= int(code) < 300)
        except Exception:
            ok = False
        finally:
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass
            try:
                gc.collect()
            except Exception:
                pass

        self._connecting = False
        self._connected = ok
        self._api_flash_on = ok
        self._status = "API online" if ok else "Can't connect"

        if _ch:
            try:
                _ch.set_api_ok(ok)
            except Exception:
                pass

        self._draw()
        return ok

    # ----------------------------------------
    # Public
    # ----------------------------------------

    def show_live(self, btn, wifi_ok=True, gps_state=None, tick_fn=None):
        """
        wifi_ok   — passed in from the carousel (WiFi already confirmed)
        gps_state — GPS_NONE / GPS_INIT / GPS_FIXED from status
        """
        btn.reset()
        self._load_cfg()

        self._wifi_ok = bool(wifi_ok)
        if gps_state is not None:
            self._gps_state = int(gps_state)
        elif _ch:
            try:
                self._gps_state = _ch.get_gps_state()
            except Exception:
                self._gps_state = GPS_NONE

        self._connected = False
        self._status = "Connecting..."
        self._draw()

        self._connect(min_ms=500)

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

            try:
                action = btn.poll_action()
            except Exception:
                action = None

            if action == "single":
                return "single"

            if action == "quad":
                return "quad"

            time.sleep_ms(25)
