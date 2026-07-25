# src/ui/screens/sailpoint.py — AS5600 sail angle indicator (MicroPython / Pico-safe)

import time

try:
    from src.ui import connection_header as _ch
except Exception:
    _ch = None


class SailpointScreen:
    def __init__(self, oled, i2c=None, sensor=None, offset_deg=0):
        self.oled = oled
        self._i2c = i2c
        self._sensor = sensor       # pre-shared AS5600 instance (optional)
        self._probed = sensor is not None
        self._offset_deg = float(offset_deg)
        self._refresh_ms = 200

    # ------------------------------------------------------------------
    # Sensor access
    # ------------------------------------------------------------------

    def _get_sensor(self):
        if self._sensor is not None:
            return self._sensor
        if self._probed:
            return None
        if self._i2c is None:
            return None
        self._probed = True
        # Re-init I2C to clear any stuck bus state left by previous sensors
        try:
            from src.hal.board import init_i2c as _init_i2c
            self._i2c = _init_i2c()
        except Exception:
            pass
        try:
            from src.drivers.as5600 import AS5600
            s = AS5600(self._i2c)
            if s.is_present:
                self._sensor = s
        except Exception:
            pass
        return self._sensor

    def _read(self):
        sensor = self._get_sensor()
        if sensor is None or not getattr(sensor, "is_present", False):
            return None
        try:
            angle = sensor.angle()
            if angle is None:
                return None
            return (angle + self._offset_deg) % 360.0
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def _draw(self, angle):
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

        # ---- Left panel: angle ring + orbiting dot -----------------------
        cx = 30
        cy = h // 2
        r  = min(cy - 3, cx - 2)   # ~28 px

        a_deg = float(angle) if angle is not None else 0.0
        draw_compass(fb, w, h, cx, cy, r, heading_deg=a_deg, ring_thick=2, dot_r=3)

        # ---- Right panel: label + degree reading -------------------------
        x_left  = cx + r + 4
        panel_w = w - x_left

        if angle is not None:
            deg_str = str(int(angle))
        else:
            deg_str = "---"

        # Measure the large angle number
        try:
            lw, lh = o._text_size(o.f_large, deg_str)
        except Exception:
            lw, lh = 24, 20

        # Measure the "SP" label (small)
        try:
            sw, sh = o._text_size(o.f_small, "SP")
        except Exception:
            sw, sh = 14, 7

        deg_sym_w = 6   # width budget for the ° glyph

        # Vertical layout (bottom-anchored, same logic as compass.py):
        #   [bottom margin 1 px]
        #   [SP label]  4 px gap
        #   [large angle number + ° glyph]
        label_y = h - sh - lh - 5
        num_y   = label_y + sh + 4

        # "SP" label — centred in right panel
        label_x = x_left + max(0, (panel_w - sw) // 2)
        o.f_small.write("SP", label_x, label_y)

        # Large angle number + degree symbol — centred in right panel
        row_w  = lw + deg_sym_w
        num_x  = x_left + max(0, (panel_w - row_w) // 2)
        o.f_large.write(deg_str, num_x, num_y)
        draw_degree(fb, num_x + lw + 1, num_y, r=2)

        fb.show()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def show_live(self, btn, tick_fn=None):
        try:
            btn.reset()
        except Exception:
            pass

        angle = self._read()
        self._draw(angle)

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
                angle = self._read()
                self._draw(angle)
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
