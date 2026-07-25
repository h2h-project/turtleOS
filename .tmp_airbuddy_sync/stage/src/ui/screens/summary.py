# src/ui/screens/summary.py — Summary screen (Pico / MicroPython safe)

import time
from src.ui.glyphs import (
    draw_circle, draw_degree, draw_c, draw_sub2, draw_face,
    draw_battery, BATT_W, BATT_H,
)

try:
    from src.ui import connection_header as _ch
except Exception:
    _ch = None

# How often the bottom-left reading rotates to the next available sensor value.
ROTATE_MS = 3000


class SummaryScreen:
    # Heartbeat glyph + room-name label, top-left.
    _GLYPH_R = 3
    _GLYPH_TEXT_GAP = 4

    def __init__(self, oled, cfg=None, i2c=None, ina=None, rtc_info=None, room_get=None):
        self.oled = oled
        self.f = getattr(oled, "f_med", None)

        self.cfg = cfg if isinstance(cfg, dict) else {}
        self._i2c = i2c
        self._ina = ina                  # pre-shared INA219 instance, or None
        self.rtc_info = rtc_info if isinstance(rtc_info, dict) else {}
        self._room_get = room_get        # callable -> room name str or None

        self.indent_x = 4

    # -------------------------------------------------
    # Classification (score + mood)
    # -------------------------------------------------
    def _score_from_reading(self, r):
        """
        Returns lvl 0..4
          0 good, 1 ok, 2 poor, 3 bad, 4 verybad
        """
        # Prefer SCD4x CO2 for scoring — consistent with what is displayed
        scd41_co2 = int(getattr(r, "scd41_co2_ppm", 0) or 0)
        ppm = scd41_co2 if scd41_co2 > 0 else int(getattr(r, "eco2_ppm", 0) or 0)
        tvoc = int(getattr(r, "tvoc_ppb", 0) or 0)
        ready = bool(getattr(r, "ready", True))

        # `ready` reflects ENS160 readiness only. If SCD41 has a valid CO2
        # reading, it is independently reliable — don't fall back to "poor".
        scd41_ok = scd41_co2 > 0
        if (not ready and not scd41_ok) or (ppm <= 0):
            return 2  # "poor" default when not ready

        # --- CO2 severity (0..4) ---
        if ppm < 800:
            co2_lvl = 0
        elif ppm < 1200:
            co2_lvl = 1
        elif ppm < 2000:
            co2_lvl = 2
        elif ppm < 5000:
            co2_lvl = 3
        else:
            co2_lvl = 4

        # --- TVOC severity (0..4) ---
        if tvoc <= 0:
            tvoc_lvl = 0
        elif tvoc < 200:
            tvoc_lvl = 0
        elif tvoc < 600:
            tvoc_lvl = 1
        elif tvoc < 2000:
            tvoc_lvl = 2
        elif tvoc < 5000:
            tvoc_lvl = 3
        else:
            tvoc_lvl = 4

        # Conservative combine: take the worse
        return co2_lvl if co2_lvl > tvoc_lvl else tvoc_lvl

    def _mood_from_score(self, lvl):
        if lvl <= 0:
            return "good"
        if lvl == 1:
            return "ok"
        if lvl == 2:
            return "poor"
        if lvl == 3:
            return "bad"
        return "verybad"

    # -------------------------------------------------
    # Reading line renderers
    # -------------------------------------------------
    def _draw_temp_line(self, temp_c, x, y):
        """
        MED: 29.7°C (degree ring pixel + C)
        """
        try:
            t = round(float(temp_c), 1)
            num = "{:.1f}".format(t)
        except Exception:
            self.f.write("--.-", x, y)
            return

        self.f.write(num, x, y)
        w_num, _ = self.oled._text_size(self.f, num)

        deg_r = 2
        deg_w = deg_r * 2 + 1
        x_deg = x + int(w_num) + 1
        draw_degree(self.oled.oled, x_deg, y + 3, r=deg_r, color=1)

        x_c = x_deg + deg_w + 1
        if not self.f.write("C", x_c, y):
            draw_c(self.oled.oled, x_c, y + 2, scale=1, color=1)

    def _draw_humidity_line(self, rh, x, y):
        """
        MED: 67%
        """
        try:
            rh_i = int(round(float(rh)))
            txt = "{}%".format(rh_i)
        except Exception:
            txt = "--%"

        self.f.write(txt, x, y)

    def _draw_co2_line(self, co2, x, y):
        """
        MED: 638 CO₂
        (CO + sub2 glyph)
        """
        try:
            n = str(int(co2))
        except Exception:
            n = "--"

        # write "<n> CO"
        base = "{} CO".format(n)
        self.f.write(base, x, y)

        w_base, _ = self.oled._text_size(self.f, base)
        # subscript sits a bit lower than baseline (tuned for MED)
        draw_sub2(self.oled.oled, x + int(w_base) + 1, y + 9, scale=1, color=1)

    def _draw_tvoc_line(self, tvoc, x, y):
        try:
            self.f.write(str(int(tvoc)) + " ppb", x, y)
        except Exception:
            self.f.write("-- ppb", x, y)

    def _draw_time_line(self, x, y):
        try:
            offset_min = self.cfg.get("timezone_offset_min", None)
        except Exception:
            offset_min = None

        try:
            if offset_min is not None:
                t = time.localtime(time.time() + int(offset_min) * 60)
            else:
                t = time.localtime()
            txt = "{:02d}:{:02d}".format(int(t[3]), int(t[4]))
        except Exception:
            txt = "--:--"

        self.f.write(txt, x, y)

    # -------------------------------------------------
    # Bottom-left rotating reading — same priority/availability protocol
    # as before (Temperature -> CO2 -> TVOC -> Humidity -> Time, first 3
    # available), but only one is shown at a time, cycling every ROTATE_MS.
    # -------------------------------------------------
    def _reading_candidates(self, r):
        # CO2: prefer SCD4x true CO2, fall back to ENS160 eCO2
        scd41_co2 = getattr(r, "scd41_co2_ppm", None) if r else None
        co2 = scd41_co2 if scd41_co2 else (getattr(r, "eco2_ppm", None) if r else None)

        # TVOC: ENS160 only
        tvoc = getattr(r, "tvoc_ppb", None) if r else None

        # Temp: prefer SCD4x, fall back to primary temp_c, then RTC temp
        scd41_temp = getattr(r, "scd41_temp_c", None) if r else None
        temp_main = scd41_temp if scd41_temp is not None else (getattr(r, "temp_c", None) if r else None)
        temp_rtc = self.rtc_info.get("temp_c") if self.rtc_info else None
        temp_val = temp_main if temp_main is not None else temp_rtc

        # Humidity: prefer SCD4x, fall back to primary humidity
        scd41_rh = getattr(r, "scd41_humidity", None) if r else None
        rh = scd41_rh if scd41_rh is not None else (getattr(r, "humidity", None) if r else None)

        ordered = (
            (temp_val is not None, lambda x, y: self._draw_temp_line(temp_val, x, y)),
            (co2 is not None and co2 > 0, lambda x, y: self._draw_co2_line(co2, x, y)),
            (tvoc is not None, lambda x, y: self._draw_tvoc_line(tvoc, x, y)),
            (rh is not None, lambda x, y: self._draw_humidity_line(rh, x, y)),
            (True, lambda x, y: self._draw_time_line(x, y)),
        )

        out = []
        for available, draw_fn in ordered:
            if available:
                out.append(draw_fn)
            if len(out) >= 3:
                break
        return out

    def _draw_bottom_reading(self, r, rotate_index):
        candidates = self._reading_candidates(r)
        if not candidates:
            return
        draw_fn = candidates[int(rotate_index) % len(candidates)]

        _, h = self.oled._text_size(self.f, "Ag")
        y = self.oled.height - h - 2
        draw_fn(self.indent_x, y)

    # -------------------------------------------------
    # Header: connection icons (top-right), heartbeat glyph + room name
    # (top-left). Footer: battery icon (bottom-right).
    # -------------------------------------------------
    def _room(self):
        if self._room_get is None:
            return None
        try:
            name = self._room_get()
        except Exception:
            return None
        if not name:
            return None
        return str(name).strip() or None

    def _fit(self, text, max_w):
        """Truncate text (from the end) until it fits within max_w pixels."""
        f_small = getattr(self.oled, "f_small", None)
        if f_small is None or max_w <= 0:
            return ""
        try:
            tw, _ = self.oled._text_size(f_small, text)
        except Exception:
            tw = len(text) * 5
        if tw <= max_w:
            return text
        s = text
        while len(s) > 1:
            s = s[:-1]
            try:
                tw, _ = self.oled._text_size(f_small, s)
            except Exception:
                tw = len(s) * 5
            if tw <= max_w:
                break
        return s

    def _battery_present(self):
        if self._ina is not None:
            return True
        if self._i2c is None:
            return False
        try:
            from src.drivers.ina219 import INA219
            ina = INA219(self._i2c, auto_init=True)
            if ina.is_present:
                self._ina = ina
                return True
        except Exception:
            pass
        return False

    def _draw_header(self, fb, beat_filled):
        w = self.oled.width

        # Top-right: connection icons.
        if _ch is not None:
            try:
                _ch.draw(fb, w, gps_state=_ch.get_gps_state(), icon_y=1)
            except Exception:
                pass

        # Top-left: heartbeat glyph + room name.
        gcx = self._GLYPH_R
        gcy = 1 + self._GLYPH_R
        draw_circle(fb, gcx, gcy, r=self._GLYPH_R, filled=beat_filled, color=1)

        room = self._room()
        f_small = getattr(self.oled, "f_small", None)
        if room and f_small is not None:
            tx = gcx + self._GLYPH_R + self._GLYPH_TEXT_GAP
            # Leave room for the connection icon cluster on the right.
            max_w = w - tx - 44
            txt = self._fit(room.upper(), max_w)
            if txt:
                f_small.write(txt, tx, 1)

    def _draw_footer(self, fb):
        # Bottom-right: battery icon, vertically aligned with the bottom-left
        # reading text row. A horizontal centre line when no INA219 is detected.
        batt_x = self.oled.width - BATT_W
        _, h = self.oled._text_size(self.f, "Ag")
        text_y = self.oled.height - h - 2
        batt_y = text_y + (h - BATT_H) // 2
        try:
            draw_battery(fb, batt_x, batt_y, no_battery=(not self._battery_present()))
        except Exception:
            pass

    # -------------------------------------------------
    # Render
    # -------------------------------------------------
    def render(self, reading, beat_filled=False, rotate_index=0):
        fb = self.oled.oled
        fb.fill(0)

        score = self._score_from_reading(reading) if reading else 2
        mood = self._mood_from_score(score)

        # Face glyph: true screen centre (both axes), sized to reach the top
        # row and bottom row of the display. Drawn first so the header/footer
        # overlays below are painted on top of it and stay legible.
        width = self.oled.width
        height = self.oled.height
        y0 = 0
        area_h = height

        draw_face(
            fb, width, height, mood,
            right_edge=False,
            fill_height_ratio=1.0,
            y0=y0,
            area_height=area_h,
        )

        self._draw_header(fb, beat_filled)
        self._draw_bottom_reading(reading, rotate_index)
        self._draw_footer(fb)

        fb.show()

    def show(self, reading):
        self.render(reading, beat_filled=False)

    # -------------------------------------------------
    # Live mode (1 second refresh, button-friendly)
    # -------------------------------------------------
    def show_live(self, get_reading, btn=None, refresh_ms=1000, max_seconds=0, tick_fn=None):
        start = time.ticks_ms()
        last_good = None
        beat = False
        _tick_next = time.ticks_ms()
        _tick_every = 500

        rotate_index = 0
        _rotate_next = time.ticks_add(time.ticks_ms(), ROTATE_MS)

        while True:
            now = time.ticks_ms()
            if tick_fn is not None and time.ticks_diff(now, _tick_next) >= 0:
                try:
                    tick_fn()
                except Exception:
                    pass
                _tick_next = time.ticks_add(now, _tick_every)

            if time.ticks_diff(now, _rotate_next) >= 0:
                rotate_index += 1
                _rotate_next = time.ticks_add(now, ROTATE_MS)

            beat = not beat

            try:
                r = get_reading()
                if r is not None:
                    last_good = r
            except Exception:
                r = None

            self.render(r if r is not None else last_good, beat_filled=beat, rotate_index=rotate_index)

            wait_start = time.ticks_ms()
            while time.ticks_diff(time.ticks_ms(), wait_start) < int(refresh_ms):
                if btn is not None:
                    try:
                        if btn.poll_action():
                            return
                    except Exception:
                        pass
                time.sleep_ms(20)

            if max_seconds and max_seconds > 0:
                if time.ticks_diff(time.ticks_ms(), start) >= int(max_seconds * 1000):
                    return
