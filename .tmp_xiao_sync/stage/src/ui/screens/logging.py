# src/ui/screens/logging.py

import time
from config import load_config, save_config
from src.ui.toggle import ToggleSwitch

try:
    from src.ui import connection_header as _ch
    from src.ui.connection_header import GPS_NONE
except Exception:
    _ch = None
    GPS_NONE = 0


class LoggingScreen:
    def __init__(self, oled):
        self.oled = oled
        self.toggle = ToggleSwitch(x=100, y=21, w=24, h=40)

        self._enabled = False
        self._post_every_s = 120
        self._api_base = ""
        self._single_grace_ms = 350

    # ----------------------------
    # Config
    # ----------------------------

    def _reload_config(self):
        cfg = load_config()
        self._enabled = bool(cfg.get("telemetry_enabled", True))
        self._post_every_s = int(cfg.get("telemetry_post_every_s", 120))
        self._api_base = str(cfg.get("api_base", "") or "")
        return cfg

    def _apply_toggle(self):
        cfg = self._reload_config()
        self._enabled = not self._enabled
        cfg["telemetry_enabled"] = self._enabled
        save_config(cfg)

    # ----------------------------
    # Drawing
    # ----------------------------

    def _draw(self):
        o = self.oled
        fb = o.oled
        fb.fill(0)

        # Connectivity icons: top-right — assume all previous values
        if _ch:
            try:
                _ch.draw(
                    fb,
                    o.width,
                    gps_state=_ch.get_gps_state(),
                    icon_y=1,
                )
            except Exception:
                pass

        o.f_arvo20.write("Telemetry", 0, 0)
        self.toggle.draw(fb, on=self._enabled)

        api_str = (self._api_base or "---")[:18]
        o.f_med.write(api_str, 0, 28)
        o.f_med.write("Post: " + str(self._post_every_s) + "s", 0, 41)

        fb.show()

    # ----------------------------
    # Public
    # ----------------------------

    def show_live(self, btn, get_queue_size=None, get_last_sent=None, tick_fn=None):
        btn.reset()

        self._reload_config()
        self._draw()

        pending_single_deadline = None
        _tick_next = time.ticks_ms()
        _tick_every = 500

        while True:
            action = btn.poll_action()
            now = time.ticks_ms()

            if tick_fn is not None and time.ticks_diff(now, _tick_next) >= 0:
                try:
                    tick_fn()
                except Exception:
                    pass
                _tick_next = time.ticks_add(now, _tick_every)

            if pending_single_deadline is not None:
                if time.ticks_diff(now, pending_single_deadline) >= 0:
                    return "single"

            if action == "single":
                pending_single_deadline = time.ticks_add(now, self._single_grace_ms)

            elif action == "double":
                pending_single_deadline = None
                self._apply_toggle()
                self._reload_config()
                self._draw()
                btn.reset()
                continue

            elif action == "quad":
                return "quad"

            elif action == "sleep":
                return "sleep"

            time.sleep_ms(25)