# src/ui/screens/compass.py  (MicroPython / Pico-safe)

import time

try:
    from src.ui import connection_header as _ch
except Exception:
    _ch = None

_LETTERS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


class CompassScreen:
    def __init__(self, oled, i2c=None, mag=None, offset_deg=0):
        self.oled = oled
        self._i2c = i2c
        self._mag = mag        # pre-shared HMC5883L instance (optional)
        self._mag_probed = mag is not None  # skip probe if a sensor was injected
        self._offset_deg = float(offset_deg)
        self._refresh_ms = 200

    # ------------------------------------------------------------------
    # Sensor access
    # ------------------------------------------------------------------

    def _get_mag(self):
        if self._mag is not None:
            return self._mag
        if self._mag_probed:
            return None  # already probed once — no hardware present
        if self._i2c is None:
            return None
        self._mag_probed = True
        # Reinitialize the I2C peripheral before probing.  finish_sampling() can
        # leave the ESP32 I2C state machine stuck (slave held SDA low during clock
        # stretching), causing silent 0x00 reads and OSError(19) writes on the next
        # device.  A fresh init_i2c() resets the hardware and releases the bus.
        try:
            from src.hal.board import init_i2c as _init_i2c
            self._i2c = _init_i2c()
        except Exception:
            pass
        # Try QMC5883L (0x0D) first — the common GY-271 clone
        try:
            from src.drivers.hmc5883l_qmc5883l import QMC5883L
            m = QMC5883L(self._i2c)
            if m.is_present:
                self._mag = m
                return self._mag
        except Exception:
            pass
        # Fall back to genuine HMC5883L (0x1E)
        try:
            from src.drivers.hmc5883l_qmc5883l import HMC5883L
            m = HMC5883L(self._i2c)
            if m.is_present:
                self._mag = m
        except Exception:
            pass
        return self._mag

    def _read(self):
        mag = self._get_mag()
        if mag is None or not getattr(mag, "is_present", False):
            return None
        try:
            raw = mag.heading()
            if raw is None:
                return None
            return (raw + self._offset_deg) % 360.0
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_letter(deg):
        return _LETTERS[int((float(deg) + 22.5) / 45.0) % 8]

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def _draw(self, heading):
        from src.ui.glyphs import draw_compass, draw_degree

        o = self.oled
        if o is None:
            return
        fb = o.oled
        fb.fill(0)

        w = int(getattr(o, "width",  128))
        h = int(getattr(o, "height",  64))

        # Connection header — top-right icons
        if _ch:
            try:
                _ch.draw(fb, w, gps_state=_ch.get_gps_state(), icon_y=1)
            except Exception:
                pass

        # ---- Left panel: compass ring ----------------------------------------
        # Ring is centred at (30, 32) with r=28, filling almost the full 64px height.
        cx = 30
        cy = h // 2          # 32
        r  = min(cy - 3, cx - 2)   # 28: top/bottom margin ≥3 px, left margin ≥2 px

        h_deg = float(heading) if heading is not None else 0.0
        draw_compass(fb, w, h, cx, cy, r, heading_deg=h_deg, ring_thick=2, dot_r=3)

        # ---- Right panel: letter bearing + degree reading --------------------
        # Panel spans from the ring's right edge to screen edge.
        x_left   = cx + r + 4   # ≈ 62
        panel_w  = w - x_left   # ≈ 66

        if heading is not None:
            letter  = self._to_letter(heading)
            deg_str = str(int(heading))
        else:
            letter  = "--"
            deg_str = "---"

        # Measure for horizontal centering
        try:
            lw, lh = o._text_size(o.f_large, letter)
        except Exception:
            lw, lh = 20, 20
        try:
            dw, dh = o._text_size(o.f_med, deg_str)
        except Exception:
            dw, dh = 20, 11

        # Vertical: anchor degree number at screen bottom, letter above it
        deg_sym_w = 6
        deg_y     = h - dh - 1          # degree number flush to bottom (1 px margin)
        block_y   = deg_y - lh - 4      # letter bearing sits above

        # Letter bearing — centred horizontally in right panel
        letter_x = x_left + max(0, (panel_w - lw) // 2)
        o.f_large.write(letter, letter_x, block_y)

        # Angular bearing + degree glyph — centred horizontally in right panel
        row_w   = dw + deg_sym_w
        deg_x   = x_left + max(0, (panel_w - row_w) // 2)
        o.f_med.write(deg_str, deg_x, deg_y)
        draw_degree(fb, deg_x + dw + 1, deg_y, r=2)

        fb.show()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def show_live(self, btn, tick_fn=None):
        try:
            btn.reset()
        except Exception:
            pass

        heading = self._read()
        self._draw(heading)

        _next_read = time.ticks_add(time.ticks_ms(), self._refresh_ms)
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

            if time.ticks_diff(now, _next_read) >= 0:
                heading = self._read()
                self._draw(heading)
                _next_read = time.ticks_add(now, self._refresh_ms)

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

            time.sleep_ms(25)
