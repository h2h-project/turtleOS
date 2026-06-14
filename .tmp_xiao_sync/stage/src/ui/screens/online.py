# src/ui/screens/online.py  (MicroPython / Pico-safe)
# WiFi is assumed already connected when this screen is shown.
# Displays API status from the background telemetry scheduler — no HTTP here.

import time

from config import load_config, save_config
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
        self._top_pad = 0

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

        self._status = "Waiting..."
        self._enabled = True
        self._api_flash_on = False
        self._wifi_ok = False
        self._gps_state = GPS_NONE

        self._load_cfg()

    # ----------------------------------------
    # Config
    # ----------------------------------------

    def _load_cfg(self):
        self.cfg = load_config()
        self.device_id = self.cfg.get("device_id", "") or ""
        self._enabled = bool(self.cfg.get("telemetry_enabled", True))

    # ----------------------------------------
    # Drawing
    # ----------------------------------------

    def _draw(self, api_state=None):
        o = self.oled
        if o is None:
            return
        fb = o.oled
        fb.fill(0)

        sending = bool(api_state.get("sending")) if api_state else False
        ok = api_state.get("ok") if api_state else None

        if _ch:
            try:
                api_icon = self._api_flash_on if sending else bool(ok)
                _ch.draw(
                    fb,
                    o.width,
                    gps_state=self._gps_state,
                    api_connected=api_icon,
                    wifi_override=self._wifi_ok,
                    api_sending=sending,
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

        # Second line: last sent elapsed, or device ID
        if api_state is not None:
            last_ms = api_state.get("last_ms")
            if last_ms is not None:
                elapsed_s = max(0, time.ticks_diff(time.ticks_ms(), last_ms) // 1000)
                if elapsed_s < 60:
                    age = "%ds ago" % elapsed_s
                elif elapsed_s < 3600:
                    age = "%dm ago" % (elapsed_s // 60)
                else:
                    age = ">1h ago"
                o.f_med.write("sent " + age, 0, status_y + line_h)
            else:
                did = (self.device_id or "---")[:14]
                o.f_med.write("ID: " + did, 0, status_y + line_h)
        else:
            did = (self.device_id or "---")[:14]
            o.f_med.write("ID: " + did, 0, status_y + line_h)

        self.toggle.draw(fb, on=self._enabled)
        fb.show()

    def _status_from_api_state(self, api_state):
        if not self._enabled:
            return "API OFF"
        if api_state is None:
            return "Waiting..."
        if api_state.get("sending"):
            return "Connecting..."
        ok = api_state.get("ok")
        if ok is True:
            return "Connected"
        if ok is False:
            return "Offline"
        return "Waiting..."

    # ----------------------------------------
    # Public
    # ----------------------------------------

    def show_live(self, btn, wifi_ok=True, gps_state=None, tick_fn=None, telemetry=None):
        """
        wifi_ok   — passed in from the carousel (WiFi already confirmed up)
        gps_state — GPS_NONE / GPS_INIT / GPS_FIXED from status
        telemetry — TelemetryState instance; provides api_state + request_now()
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

        # Ask scheduler to send immediately so user sees a live result.
        # Show "Connecting..." right away — the tick will update to Connected/Offline
        # after the send completes (within the next 500ms tick window).
        _requested = False
        if telemetry is not None and self._enabled:
            try:
                telemetry.request_now()
                _requested = True
            except Exception:
                pass

        api_state = telemetry.api_state if telemetry is not None else None
        if _requested:
            self._status = "Connecting..."
        else:
            self._status = self._status_from_api_state(api_state)
        self._draw(api_state)

        _tick_next = time.ticks_ms()
        _tick_every = 500
        _flash_next = time.ticks_ms()

        while True:
            now = time.ticks_ms()

            if tick_fn is not None and time.ticks_diff(now, _tick_next) >= 0:
                try:
                    tick_fn()
                except Exception:
                    pass
                _tick_next = time.ticks_add(now, _tick_every)

                # Refresh display after background tick (scheduler may have updated api_state)
                api_state = telemetry.api_state if telemetry is not None else None
                self._status = self._status_from_api_state(api_state)
                self._draw(api_state)

            # Flash icon while sending
            if telemetry is not None:
                sending = bool((telemetry.api_state or {}).get("sending"))
                if sending and time.ticks_diff(now, _flash_next) >= 0:
                    self._api_flash_on = not self._api_flash_on
                    _flash_next = time.ticks_add(now, 250)
                    self._draw(telemetry.api_state)

            try:
                action = btn.poll_action()
            except Exception:
                action = None

            if action == "single":
                return "single"

            if action == "quad":
                return "quad"

            if action == "sleep":
                return "sleep"

            if action == "double":
                # Toggle telemetry_enabled
                self._load_cfg()
                self._enabled = not self._enabled
                try:
                    self.cfg["telemetry_enabled"] = self._enabled
                    save_config(self.cfg)
                except Exception:
                    pass
                api_state = telemetry.api_state if telemetry is not None else None
                self._status = self._status_from_api_state(api_state)
                self._draw(api_state)

            time.sleep_ms(25)
