# src/ui/screens/eco2.py
# eCO₂ screen for AirBuddy (RAM-lean)
# Pico / MicroPython safe

import gc
import time
from src.ui.thermobar import ThermoBar
from src.ui.glyphs import draw_sub2, draw_face9


class eCO2Screen:
    DISPLAY_DURATION = 4
    REFRESH_MS = 4000
    POLL_MS = 25

    # ppm -> step mapping edges (11 points = 10 bins)
    _EDGES = (400, 550, 700, 850, 1000, 1300, 1600, 2000, 2600, 3400, 5000)

    # Fixed UI strings (avoid re-alloc each call)
    _TXT_ECO = "eCO"
    _TXT_PPM = "PPM"
    _TXT_CONF = "CONF"
    _TXT_NA = "SENSOR NOT READY"
    _TXT_XX = "XX%"

    # Fixed scale labels
    _SCALE_TXT = ("400", "1000", "2000", "5000")
    _SCALE_X = (2, 40, 78, 108)

    # Bottom labels
    _LBL_TXT = ("OK", "MEH", "BAD")
    _LBL_X = (30, 56, 82)

    def __init__(self, oled):
        self.oled = oled

        # Fonts
        self.f_arvo16 = getattr(oled, "f_arvo16", None)
        self.f_med = getattr(oled, "f_med", None)
        self.f_large = getattr(oled, "f_large", None)
        self.f_small = getattr(oled, "f_small", None)
        self.w_small = self.f_small

        # Bar
        self.bar = ThermoBar(oled)

        # Layout
        w = int(self.oled.width)
        h = int(self.oled.height)

        self.bar_x = 2
        self.bar_w = w - 4

        self.scale_y = 34
        self.bar_y = 45

        self.faces_y = 0 if h < 9 else (h - 9)

        # labels baseline aligned to vsmall height
        _, h_vs = self.oled._text_size(self.f_small, "Ag")
        self.labels_y = self.faces_y + 9 - h_vs
        if self.labels_y < 0:
            self.labels_y = 0

        self.left_face = "good"
        self.right_face = "verybad"
        self.left_face_x = 2
        self.right_face_x = 110

        # Precompute fixed widths (avoid repeated .size() calls)
        self._w_eco, _ = (self.f_arvo16 if self.f_arvo16 else self.f_med).size(self._TXT_ECO)
        self._w_ppm, _ = self.w_small.size(self._TXT_PPM)
        self._scale_w = []
        for t in self._SCALE_TXT:
            tw, _ = self.w_small.size(t)
            self._scale_w.append(int(tw))

        # Precompute inner limits (must match ThermoBar inset logic)
        inner_x = self.bar_x + 2
        inner_w = self.bar_w - 4
        if inner_w < 1:
            inner_w = 1
        self._inner_lo = inner_x
        self._inner_hi = inner_x + inner_w - 1

        # Precompute tick X positions aligned to label centers + tiny offsets
        # (replaces dict + index lookups)
        self._tick_x = []
        for i in range(4):
            x_label = int(self._SCALE_X[i])
            x_center = x_label + (self._scale_w[i] // 2)

            # manual nudges (kept from your tuning)
            if i == 1:      # 1000
                x_center += 5
            elif i == 2:    # 2000
                x_center += -1
            elif i == 3:    # 5000
                x_center += -2

            # clamp
            if x_center < self._inner_lo:
                x_center = self._inner_lo
            elif x_center > self._inner_hi:
                x_center = self._inner_hi

            self._tick_x.append(int(x_center))

    # -------------------------------------------------
    # Public API
    # -------------------------------------------------
    def show(self, reading, confidence_pct=None):
        self.oled.clear()

        # free memory right before first draw/write burst
        gc.collect()

        ppm = int(getattr(reading, "eco2_ppm", 0))
        ready = bool(getattr(reading, "ready", True))
        not_ready = (not ready) or (ppm <= 0)

        # Confidence (keep lean: avoid format())
        conf = None
        if confidence_pct is not None:
            try:
                conf = int(confidence_pct)
            except Exception:
                conf = None
        else:
            try:
                conf = int(getattr(reading, "confidence"))
            except Exception:
                conf = None

        if conf is None:
            conf_text = self._TXT_XX
        else:
            if conf < 0:
                conf = 0
            elif conf > 100:
                conf = 100
            conf_text = str(conf) + "%"

        self._draw_header(ppm, conf_text, not_ready)
        self._draw_scale_numbers()
        self._draw_bar(ppm, not_ready)
        self._draw_fixed_ticks()
        self._draw_bottom_labels()

        self.oled.oled.show()

    def show_live(self, btn=None, get_reading=None, refresh_ms=None, tick_fn=None):
        """
        Live refresh screen — redraws every REFRESH_MS (default 5 s).

        get_reading: callable() -> reading object with eco2_ppm / confidence attributes.
        Exit: single click only.
        tick_fn: optional background callable (e.g. telemetry tick), called every 500 ms.
        """
        if refresh_ms is None:
            refresh_ms = self.REFRESH_MS

        if get_reading is None:
            def get_reading():
                return None

        reading = None
        next_refresh = 0
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

            if time.ticks_diff(now, next_refresh) >= 0:
                try:
                    reading = get_reading()
                except Exception:
                    reading = None

                gc.collect()
                ppm = int(getattr(reading, "eco2_ppm", 0)) if reading else 0
                ready = bool(getattr(reading, "ready", True)) if reading else False
                not_ready = (not ready) or (ppm <= 0)

                conf = None
                if reading is not None:
                    try:
                        conf = int(getattr(reading, "confidence"))
                    except Exception:
                        conf = None
                if conf is None:
                    conf_text = self._TXT_XX
                else:
                    if conf < 0:
                        conf = 0
                    elif conf > 100:
                        conf = 100
                    conf_text = str(conf) + "%"

                self.oled.oled.fill(0)
                self._draw_header(ppm, conf_text, not_ready)
                self._draw_scale_numbers()
                self._draw_bar(ppm, not_ready)
                self._draw_fixed_ticks()
                self._draw_bottom_labels()
                self.oled.oled.show()

                next_refresh = time.ticks_add(now, int(refresh_ms))

            action = None
            if btn is not None:
                try:
                    action = btn.poll_action()
                except Exception:
                    action = None

            if action:
                return action

            time.sleep_ms(self.POLL_MS)

    # -------------------------------------------------
    # Drawing helpers
    # -------------------------------------------------
    def _draw_header(self, ppm, conf_text, not_ready):
        x0 = 2
        y_title = 2
        status_y = 19

        title_writer = self.f_arvo16 if self.f_arvo16 else self.f_med

        # Title (no faux-bold)
        title_writer.write(self._TXT_ECO, x0, y_title)

        sub2_x = x0 + int(self._w_eco) + 1
        sub2_y = y_title + 10
        draw_sub2(self.oled.oled, sub2_x, sub2_y, scale=1, color=1)

        # "PPM"
        ppm_x = sub2_x + 6
        ppm_y = y_title + (4 if self.w_small is self.f_small else 5)
        self.w_small.write(self._TXT_PPM, ppm_x, ppm_y)

        # Status
        if not_ready:
            self.f_med.write(self._TXT_NA, x0, status_y)
        else:
            yy = status_y + (1 if self.w_small is self.f_small else 0)
            self.w_small.write(conf_text, x0, yy)
            w_pct, _ = self.w_small.size(conf_text)
            self.w_small.write(self._TXT_CONF, x0 + int(w_pct) + 6, yy)

        # Value (only when ready)
        if not not_ready:
            val = str(int(ppm))
            tw, _ = self.f_large.size(val)
            x = int(self.oled.width) - int(tw) - 2
            self.f_large.write(val, x, 2)

    def _draw_scale_numbers(self):
        y = self.scale_y
        w = self.w_small
        # manual loop avoids zip() iterator object
        w.write(self._SCALE_TXT[0], self._SCALE_X[0], y)
        w.write(self._SCALE_TXT[1], self._SCALE_X[1], y)
        w.write(self._SCALE_TXT[2], self._SCALE_X[2], y)
        w.write(self._SCALE_TXT[3], self._SCALE_X[3], y)

    def _ppm_to_step_p(self, ppm):
        edges = self._EDGES
        if ppm <= edges[0]:
            return 0.0
        if ppm >= edges[-1]:
            return 1.0

        # 10 bins
        for i in range(10):
            if edges[i] <= ppm < edges[i + 1]:
                return (i + 1) / 10.0
        return 1.0

    def _draw_bar(self, ppm, not_ready):
        p = 0.0 if not_ready else self._ppm_to_step_p(int(ppm))

        self.bar.draw(
            x=self.bar_x,
            y=self.bar_y,
            w=self.bar_w,
            p=p,
            outline=True,
            clear_bg=False
        )

        # Pointer at end of fill
        if not not_ready:
            x_end = self.bar_x + int(round(p * (self.bar_w - 1)))
            if x_end < self.bar_x:
                x_end = self.bar_x
            elif x_end > (self.bar_x + self.bar_w - 1):
                x_end = self.bar_x + self.bar_w - 1
            self.oled.oled.pixel(x_end, self.bar_y - 1, 1)

    def _draw_tick(self, x):
        # 6px tall; starts 2px above border
        y0 = self.bar_y - 2
        fb = self.oled.oled
        x = int(x)
        fb.pixel(x, y0 + 0, 1)
        fb.pixel(x, y0 + 1, 1)
        fb.pixel(x, y0 + 2, 1)
        fb.pixel(x, y0 + 3, 1)
        fb.pixel(x, y0 + 4, 1)
        fb.pixel(x, y0 + 5, 1)

    def _draw_fixed_ticks(self):
        # 4 ticks at the 4 scale labels
        tx = self._tick_x
        self._draw_tick(tx[0])
        self._draw_tick(tx[1])
        self._draw_tick(tx[2])
        self._draw_tick(tx[3])

    def _draw_bottom_labels(self):
        y_face = self.faces_y
        y_text = self.labels_y

        draw_face9(self.oled.oled, int(self.left_face_x), int(y_face), mood=self.left_face, scale=1, color=1)

        w = self.f_small
        w.write(self._LBL_TXT[0], self._LBL_X[0], y_text)
        w.write(self._LBL_TXT[1], self._LBL_X[1], y_text)
        w.write(self._LBL_TXT[2], self._LBL_X[2], y_text)

        draw_face9(self.oled.oled, int(self.right_face_x), int(y_face), mood=self.right_face, scale=1, color=1)
