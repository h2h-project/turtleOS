# src/ui/screens/tvoc.py
# TVOC screen for AirBuddy
# Pico / MicroPython safe

import gc
import time
from src.ui.thermobar import ThermoBar
from src.ui.glyphs import draw_face9


class TVOCScreen:
    """
    TVOC (ppb) screen (128x64), aligned to CO2Screen template.

    Scale (ppb) reference (common guidance-ish):
      0      220      660      2200     5500
      :-)     OK      POOR      BAD      xx-(

    Confidence:
      - uses reading.confidence if present
      - if confidence_pct passed in, it overrides reading.confidence
      - if nothing available, shows "XX% CONF"
    """

    DISPLAY_DURATION = 4
    REFRESH_MS = 4000
    POLL_MS = 25

    def __init__(self, oled):
        self.oled = oled

        # Fonts (match CO2)
        self.f_arvo = getattr(oled, "f_arvo", None)
        self.f_med = oled.f_med
        self.f_large = oled.f_large
        self.f_small = getattr(oled, "f_small", None)
        self.f_vs = oled.f_vsmall

        # Bar
        self.bar = ThermoBar(oled)

        # ----------------------------
        # Scale numbers above bar (ppb)
        # ----------------------------
        self.scale_ppb = [200, 600, 2000, 5000]
        self.scale_x = [2, 40, 82, 104]
        self.tick_ppb = [200, 600, 2000, 5000]


# Layout tuning (match CO2)
        self.bar_x = 2
        self.bar_w = int(self.oled.width) - 4

        self.scale_y = 34
        self.bar_y = 45  # lowered by 2px to avoid touching numbers

        # Bottom labels (faces at ends + text middle)
        self.left_face = "good"
        self.right_face = "verybad"

        self.faces_y = max(0, int(self.oled.height) - 9)
        _, h_vs = self.oled._text_size(self.f_vs, "Ag")
        self.labels_y = max(0, self.faces_y + 9 - h_vs)

        self.left_face_x = 2
        self.label_texts = ["OK", "POOR", "BAD"]
        self.label_x = [28, 56, 86]
        self.right_face_x = 110

        # Optional per-tick pixel nudges (like your CO2 trick)
        self.tick_offset = {
            # 220: 2,
            # 660: -1,
            # 2200: -2,
            # 5500: -6,
        }

    # -------------------------------------------------
    # Public API
    # -------------------------------------------------
    def show(self, reading, confidence_pct=None):
        self.oled.clear()

        tvoc = int(getattr(reading, "tvoc_ppb", 0))
        ready = bool(getattr(reading, "ready", True))

        # Confidence
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
            conf_text = "XX%"
        else:
            conf = 0 if conf < 0 else 100 if conf > 100 else conf
            conf_text = "{}%".format(conf)

        not_ready = (not ready) or (tvoc <= 0)

        self._draw_header(tvoc, conf_text, not_ready)
        self._draw_scale_numbers()
        self._draw_bar(tvoc, not_ready)
        self._draw_fixed_ticks()
        self._draw_bottom_labels()

        self.oled.oled.show()

    def show_live(self, btn=None, get_reading=None, refresh_ms=None, tick_fn=None):
        """
        Live refresh screen — redraws every REFRESH_MS (default 4 s).

        get_reading: callable() -> reading object with tvoc_ppb / confidence attributes.
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
                tvoc = int(getattr(reading, "tvoc_ppb", 0)) if reading else 0
                ready = bool(getattr(reading, "ready", True)) if reading else False
                not_ready = (not ready) or (tvoc <= 0)

                conf = None
                if reading is not None:
                    try:
                        conf = int(getattr(reading, "confidence"))
                    except Exception:
                        conf = None
                if conf is None:
                    conf_text = "XX%"
                else:
                    conf = 0 if conf < 0 else 100 if conf > 100 else conf
                    conf_text = str(conf) + "%"

                self.oled.oled.fill(0)
                self._draw_header(tvoc, conf_text, not_ready)
                self._draw_scale_numbers()
                self._draw_bar(tvoc, not_ready)
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
    def _draw_header(self, tvoc, conf_text, not_ready):
        x0 = 2
        y0 = 0

        # Title "TVOC" using Arvo16 if available, else MED
        title_writer = self.f_arvo if self.f_arvo else self.f_med
        title_writer.write("TVOC", x0, y0)

        # Unit "PPB" in SMALL (fallback VSMALL) — same style as CO2
        w_main, _ = title_writer.size("TVOC")
        unit_writer = self.f_small if self.f_small else self.f_vs
        unit_x = x0 + int(w_main) + 6
        unit_y = y0 + (4 if unit_writer is self.f_small else 5)
        unit_writer.write("PPB", unit_x, unit_y)

        # Status / confidence line (raised back up)
        status_y = 14  # original position

        if not_ready:
            self.f_med.write("SENSOR NOT READY", x0, status_y)
        else:
            conf_writer = self.f_small if self.f_small else self.f_vs
            conf_y = status_y  # no extra offset now

            conf_writer.write(conf_text, x0, conf_y)
            w_pct, _ = conf_writer.size(conf_text)
            conf_writer.write("CONF", x0 + int(w_pct) + 6, conf_y)


        # Value top-right, LARGE (only when ready)
        if not not_ready:
            val = str(int(tvoc))
            tw, _ = self.f_large.size(val)
            x = int(self.oled.width) - int(tw) - 2
            y = 2
            self.f_large.write(val, x, y)

    def _draw_scale_numbers(self):
        # numbers above bar should be SMALL (fallback to VSMALL)
        writer = self.f_small if self.f_small else self.f_vs
        y = self.scale_y
        for v, x in zip(self.scale_ppb, self.scale_x):
            writer.write(str(v), int(x), int(y))

    def _tvoc_to_step_p(self, tvoc):
        """
        Map TVOC ppb into 10 discrete fill positions (0.0 .. 1.0 in steps of 0.1)
        using bins aligned to your scale points.

        Bins:
          0..220..660..2200..5500 spread into 10 steps.
        """
        edges = [0, 60, 120, 180, 220, 400, 660, 1200, 2200, 3500, 5500]

        if tvoc <= edges[0]:
            return 0.0
        if tvoc >= edges[-1]:
            return 1.0

        for i in range(10):
            if edges[i] <= tvoc < edges[i + 1]:
                return (i + 1) / 10.0

        return 1.0

    def _draw_bar(self, tvoc, not_ready):
        p = 0.0 if not_ready else self._tvoc_to_step_p(int(tvoc))

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
            x_end = max(self.bar_x, min(self.bar_x + self.bar_w - 1, x_end))
            self.oled.oled.pixel(x_end, self.bar_y - 1, 1)

    def _inner_track_limits(self):
        # Must match ThermoBar inner geometry: inner_x=x+2 ; inner_w=w-4
        inner_x = self.bar_x + 2
        inner_w = self.bar_w - 4
        lo = inner_x
        hi = inner_x + inner_w - 1
        return lo, hi

    def _tick_x_for_label_center(self, v):
        writer = self.f_small if self.f_small else self.f_vs

        try:
            i = self.scale_ppb.index(v)
            x_label = int(self.scale_x[i])
        except Exception:
            x_label = 0

        txt = str(v)
        tw, _ = writer.size(txt)
        x_center = x_label + (int(tw) // 2)

        # manual nudges (optional)
        x_center += int(self.tick_offset.get(v, 0))

        lo, hi = self._inner_track_limits()
        if x_center < lo:
            x_center = lo
        if x_center > hi:
            x_center = hi

        return x_center

    def _draw_tick(self, x):
        # 1px taller than above the top border + crosses into bar
        y0 = self.bar_y - 2
        for yy in range(6):  # 6px tall
            self.oled.oled.pixel(int(x), int(y0 + yy), 1)

    def _draw_fixed_ticks(self):
        for v in self.tick_ppb:
            x = self._tick_x_for_label_center(v)
            self._draw_tick(x)

    def _draw_bottom_labels(self):
        y_face = self.faces_y
        y_text = self.labels_y

        draw_face9(self.oled.oled, int(self.left_face_x), int(y_face), mood=self.left_face, scale=1, color=1)

        for txt, x in zip(self.label_texts, self.label_x):
            self.f_vs.write(txt, int(x), int(y_text))

        draw_face9(self.oled.oled, int(self.right_face_x), int(y_face), mood=self.right_face, scale=1, color=1)
