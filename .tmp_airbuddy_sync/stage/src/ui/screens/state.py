# src/ui/screens/state.py — machine-state screen (turtle_mode)
#
# Three circles across the 128x64 OLED:
#   HDG  — compass bearing, running-ball style (same glyph as compass.py)
#   SAIL — AS5600 sail position as a line slicing through the circle
#   WIND — detected wind direction as a rotating arrow ("?" until the
#          first luff sweep solves it; progress % while sweeping)
#
# The screen hosts the autopilot while open: nav.tick() runs every loop
# iteration (the controller self-rate-limits). Double-click in ACQUIRE
# starts the first luff sweep; completion transitions to SAIL-NAV in
# NavController and the header simply re-renders. Double-click in
# SAIL-NAV triggers a manual re-sweep.
#
# NOTE: this is the UI screen; the nav-state singleton lives in
# src/nav/state_machine.py — keep the names distinct.

import time

try:
    from src.ui import connection_header as _ch
except Exception:
    _ch = None


class StateScreen:
    def __init__(self, oled, nav=None, cfg_get=None):
        self.oled = oled
        self._nav = nav
        self._cfg_get = cfg_get
        self._refresh_ms = 200

    # ------------------------------------------------------------------

    def _cfg(self):
        if self._cfg_get is not None:
            try:
                return self._cfg_get() or {}
            except Exception:
                pass
        return {}

    def _snapshot(self):
        if self._nav is None:
            return None
        try:
            return self._nav.snapshot()
        except Exception:
            return None

    # ------------------------------------------------------------------

    def _draw_unknown(self, o, fb, w, h, cx, cy):
        try:
            qw, qh = o._text_size(o.f_small, "?")
        except Exception:
            qw, qh = 5, 7
        o.f_small.write("?", cx - qw // 2, cy - qh // 2)

    def _draw(self, snap, sweep=None):
        from src.ui.glyphs import (
            draw_compass, draw_diameter_line, draw_arrow_in_circle,
            _f_circle_outline,
        )
        from src.nav.state_machine import display_name

        o = self.oled
        if o is None:
            return
        fb = o.oled
        fb.fill(0)
        w = int(getattr(o, "width", 128))
        h = int(getattr(o, "height", 64))

        # Header: state name left, connection icons right
        o.f_small.write(display_name(), 0, 1)
        if _ch:
            try:
                _ch.draw(fb, w, gps_state=_ch.get_gps_state(), icon_y=1)
            except Exception:
                pass

        R = 18
        CY = 36
        sweeping = bool(sweep) and sweep.get("phase") not in ("idle", "done", "failed")

        # --- Circle 1: heading (running ball) ---
        heading = snap.get("heading") if snap else None
        if heading is not None:
            draw_compass(fb, w, h, 21, CY, R, heading_deg=heading,
                         ring_thick=1, dot_r=2)
        else:
            _f_circle_outline(fb, w, h, 21, CY, R, 1)
            self._draw_unknown(o, fb, w, h, 21, CY)

        # --- Circle 2: sail position (diameter line) ---
        sail = snap.get("sail") if snap else None
        if sail is not None:
            draw_diameter_line(fb, w, h, 64, CY, R, sail)
        else:
            _f_circle_outline(fb, w, h, 64, CY, R, 1)
            self._draw_unknown(o, fb, w, h, 64, CY)

        # --- Circle 3: wind direction (arrow) ---
        wind = snap.get("wind") if snap else None
        if sweeping and sweep.get("sail_deg") is not None:
            # sweep in progress: arrow tracks the sweeping sail
            draw_arrow_in_circle(fb, w, h, 107, CY, R, sweep["sail_deg"])
        elif wind is not None:
            draw_arrow_in_circle(fb, w, h, 107, CY, R, wind)
        else:
            _f_circle_outline(fb, w, h, 107, CY, R, 1)
            self._draw_unknown(o, fb, w, h, 107, CY)

        # --- Labels ---
        labels = ("HDG", "SAIL",
                  "{}%".format(sweep.get("progress", 0)) if sweeping else "WIND")
        for cx, txt in zip((21, 64, 107), labels):
            try:
                tw, _ = o._text_size(o.f_small, txt)
            except Exception:
                tw = len(txt) * 5
            o.f_small.write(txt, cx - tw // 2, 56)

        fb.show()

    # ------------------------------------------------------------------

    def show_live(self, btn, tick_fn=None):
        try:
            btn.reset()
        except Exception:
            pass

        snap = self._snapshot()
        sweep = self._nav.sweep_status() if self._nav else None
        self._draw(snap, sweep)

        _next_draw = time.ticks_add(time.ticks_ms(), self._refresh_ms)
        _tick_next = time.ticks_ms()
        _tick_every = 500

        while True:
            now = time.ticks_ms()

            # Autopilot: every iteration; NavController self-rate-limits
            # (50 ms cadence while sweeping, nav_cycle_ms otherwise).
            if self._nav is not None:
                try:
                    self._nav.tick(self._cfg())
                except Exception:
                    pass

            if tick_fn is not None and time.ticks_diff(now, _tick_next) >= 0:
                try:
                    tick_fn()
                except Exception:
                    pass
                _tick_next = time.ticks_add(now, _tick_every)

            if time.ticks_diff(now, _next_draw) >= 0:
                snap = self._snapshot()
                sweep = self._nav.sweep_status() if self._nav else None
                self._draw(snap, sweep)
                _next_draw = time.ticks_add(now, self._refresh_ms)

            try:
                action = btn.poll_action()
            except Exception:
                action = None

            if action == "double" and self._nav is not None:
                # ACQUIRE: first luff sweep (entry to SAIL-NAV on completion)
                # SAIL-NAV: manual re-sweep. Ignored while a sweep is active.
                try:
                    if not self._nav.sweeping():
                        self._nav.begin_luff_sweep()
                except Exception:
                    pass
            elif action == "single":
                return "single"
            elif action == "quad":
                return "quad"
            elif action == "sleep":
                return "sleep"

            time.sleep_ms(25)
