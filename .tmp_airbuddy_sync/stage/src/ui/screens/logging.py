# src/ui/screens/logging.py

import time
from config import load_config, save_config
from src.ui.toggle import ToggleSwitch
from src.ui import grace as _grace

try:
    from src.ui import connection_header as _ch
    from src.ui.connection_header import GPS_NONE
except Exception:
    _ch = None
    GPS_NONE = 0

# Double-click cycle order for the tri-state toggle. Index doubles as the
# knob position passed to ToggleSwitch.draw(pos=...): 0=bottom, 1=mid, 2=top.
MODE_CYCLE = ("off", "auto", "manual")
MODE_LABELS = {"off": "Off", "auto": "Auto", "manual": "Manual"}


class LoggingScreen:
    def __init__(self, oled):
        self.oled = oled

        w = int(getattr(oled, "width", 128))
        h = int(getattr(oled, "height", 64))

        # Match the Online screen's toggle geometry exactly — same x/y/w/h,
        # tallest the switch can be without running off the bottom edge.
        tx = 100
        ty = 16
        tw = 24
        th = 43
        if tx + tw > w:
            tw = max(1, w - tx)
        if ty + th > h:
            th = max(1, h - ty)

        self.toggle = ToggleSwitch(x=tx, y=ty, w=tw, h=th)

        self._mode = "auto"
        self._post_every_s = 120
        self._api_base = ""
        self._single_grace_ms = 350

        # Live config dict handed in by the carousel, so a mode change takes
        # effect on the very next background tick instead of after the carousel
        # exits and the main loop re-reads config.json.
        self._live_cfg = None

    # ----------------------------
    # Config
    # ----------------------------

    def _reload_config(self):
        cfg = load_config()
        self._mode = self._norm_mode(cfg.get("telemetry_mode", "auto"))
        self._post_every_s = int(cfg.get("telemetry_post_every_s", 120))
        self._api_base = str(cfg.get("api_base", "") or "")
        return cfg

    @staticmethod
    def _norm_mode(val):
        try:
            m = str(val or "").strip().lower()
        except Exception:
            m = ""
        return m if m in MODE_CYCLE else "auto"

    def _cycle_mode(self):
        """Advance off -> auto -> manual -> off and persist."""
        cfg = self._reload_config()
        try:
            nxt = MODE_CYCLE[(MODE_CYCLE.index(self._mode) + 1) % len(MODE_CYCLE)]
        except Exception:
            nxt = "auto"

        self._mode = nxt
        cfg["telemetry_mode"] = nxt
        cfg["telemetry_enabled"] = (nxt != "off")
        save_config(cfg)

        # Push into the live dict the background tick reads from.
        if isinstance(self._live_cfg, dict):
            self._live_cfg["telemetry_mode"] = nxt
            self._live_cfg["telemetry_enabled"] = (nxt != "off")

    # ----------------------------
    # Drawing
    # ----------------------------

    @staticmethod
    def _queue_size():
        try:
            from src.app.telemetry_scheduler import TelemetryScheduler
            return TelemetryScheduler.queue_size()
        except Exception:
            return 0

    def _fit(self, text, max_w):
        """Truncate text (from the end) until it fits within max_w pixels."""
        o = self.oled
        f_small = getattr(o, "f_small", None)
        if f_small is None or max_w <= 0:
            return text
        try:
            tw, _ = o._text_size(f_small, text)
        except Exception:
            tw = len(text) * 5
        if tw <= max_w:
            return text
        s = text
        while len(s) > 1:
            s = s[:-1]
            try:
                tw, _ = o._text_size(f_small, s)
            except Exception:
                tw = len(s) * 5
            if tw <= max_w:
                break
        return s

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
        try:
            _pos = MODE_CYCLE.index(self._mode)
        except Exception:
            _pos = 1
        self.toggle.draw(fb, pos=_pos)

        # Status line — current font (f_med), directly under the title.
        mode_label = MODE_LABELS.get(self._mode, "Auto")
        _, title_h = o._text_size(o.f_arvo20, "Telemetry")
        y_status = title_h + 2
        o.f_med.write(mode_label, 0, y_status)

        # Detail lines — API base, post frequency, unsynced — shrunk to the
        # smallest available font (f_small) to make room for the status line.
        # Fixed probe string so row height (and therefore the layout below)
        # does not shift as the mode label changes.
        _, row_h = o._text_size(o.f_med, "Ag")
        _, small_h = o._text_size(o.f_small, "Ag")
        y1 = y_status + row_h + 3
        y2 = y1 + small_h + 3
        y3 = y2 + small_h + 3

        # f_small is narrow enough that the API base line can run right up
        # to the toggle switch instead of stopping at a fixed char count.
        api_max_w = self.toggle.x - 4
        api_str = self._fit(self._api_base or "---", api_max_w)
        o.f_small.write(api_str, 0, y1)
        if self._mode == "manual":
            post_str = "Post: on demand"
        elif self._mode == "off":
            post_str = "Post: --"
        else:
            post_str = "Post: " + str(self._post_every_s) + "s"
        o.f_small.write(post_str, 0, y2)
        o.f_small.write("Unsynced: " + str(self._queue_size()), 0, y3)

        fb.show()

    # ----------------------------
    # Public
    # ----------------------------

    def show_live(self, btn, get_queue_size=None, get_last_sent=None, tick_fn=None, cfg=None):
        btn.reset()

        self._live_cfg = cfg if isinstance(cfg, dict) else None
        self._reload_config()
        self._draw()

        # Give the user a 2s window to single-click straight past this
        # screen before anything else runs — matches Online/WiFi so the
        # carousel feels consistent even though this screen has nothing to
        # check (queue size is read locally, not over the network).
        while True:
            grace_action = _grace.await_grace_window(btn, tick_fn, ms=2000)
            if grace_action is None:
                break
            if grace_action == "double":
                self._cycle_mode()
                self._draw()
                btn.reset()
                continue
            return grace_action

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
                self._cycle_mode()
                self._draw()
                btn.reset()
                continue

            elif action == "quad":
                return "quad"

            elif action == "sleep":
                return "sleep"

            time.sleep_ms(25)