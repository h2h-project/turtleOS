# src/ui/thermobar.py  (MicroPython / Pico-safe)
#
# A reusable “thermo bar” UI primitive for SSD1306 framebuf displays.
#
# Default spec:
#   - Total height: 7px
#   - 1px border
#   - 5px inner fill area
#   - Rounded corners (simple 2px-radius look)
#   - Inner fill uses checkerboard dithering
#   - Leading edge of the fill is a solid vertical line
#
# Upgrades:
#   - Construct with x/y/width/height (keyword-friendly)
#   - Draw by progress p (0..1) OR draw by value mapped to a range
#   - Optional indicator tick (solid vertical line) marking current value
#   - Optional “breathing” center-fill mode for spinners/uncertainty bars
#   - Backwards-compatible: draw(x,y,w,p,...) still works
#
# Usage examples:
#   bar = ThermoBar(oled, x=10, y=40, width=108)   # default height=7
#   bar.draw(p=0.65)                               # uses stored x/y/width
#   bar.draw_value(value=820, vmin=100, vmax=2000) # maps to p
#   bar.draw(p=0.5, mode="center")                 # center-expanding fill


class ThermoBar:
    def __init__(self, oled, x=0, y=0, width=100, height=7, invert=False):
        """
        oled: your OLED helper. Must expose FrameBuffer as oled.oled with
              .pixel/.hline/.vline/.rect/.fill_rect
        x,y,width,height: default geometry for draw()
        invert: if True, swaps colors (useful for inverted themes)
        """
        self.oled = oled
        self.invert = invert

        self.x = int(x)
        self.y = int(y)
        self.width = int(width)
        self.height = int(height)

    # ----------------------------
    # Internal framebuffer helpers
    # ----------------------------
    def _fb(self):
        return getattr(self.oled, "oled", None)

    def _c(self, on):
        # 1-bit display color helper
        if self.invert:
            return 0 if on else 1
        return 1 if on else 0

    def _pixel(self, x, y, on):
        fb = self._fb()
        if fb:
            fb.pixel(int(x), int(y), self._c(on))

    def _hline(self, x, y, w, on):
        fb = self._fb()
        if fb and hasattr(fb, "hline"):
            fb.hline(int(x), int(y), int(w), self._c(on))

    def _vline(self, x, y, h, on):
        fb = self._fb()
        if fb and hasattr(fb, "vline"):
            fb.vline(int(x), int(y), int(h), self._c(on))

    def _fill_rect(self, x, y, w, h, on):
        fb = self._fb()
        if fb and hasattr(fb, "fill_rect"):
            fb.fill_rect(int(x), int(y), int(w), int(h), self._c(on))

    # ----------------------------
    # Geometry helpers
    # ----------------------------
    def _clamp(self, v, lo, hi):
        if v < lo:
            return lo
        if v > hi:
            return hi
        return v

    def _inner_geom(self, x, y, w, h):
        """
        Inner geometry:
          - 1px border top/bottom => inner band is y+1 .. y+h-2
          - 2px horizontal inset => inner_x = x+2 ; inner_w = w-4
        """
        # keep minimums sane
        if h < 7:
            h = 7
        if w < 10:
            w = 10

        inner_y = y + 1
        inner_h = h - 2  # should be 5 when h=7

        inner_x = x + 2
        inner_w = w - 4

        if inner_w < 1:
            inner_w = 1
        if inner_h < 1:
            inner_h = 1

        return inner_x, inner_y, inner_w, inner_h

    # ----------------------------
    # Rounded outline (2px-radius look)
    # ----------------------------
    def _round_rect_outline(self, x, y, w, h, on):
        # Radius=2 look: "cut" the extreme corner pixels and draw near-corner pixels.
        if w < 4 or h < 4:
            fb = self._fb()
            if fb and hasattr(fb, "rect"):
                fb.rect(int(x), int(y), int(w), int(h), self._c(on))
            return

        # Top/bottom lines (leave 2px for rounding)
        self._hline(x + 2, y, w - 4, on)
        self._hline(x + 2, y + h - 1, w - 4, on)

        # Left/right lines (leave 2px for rounding)
        self._vline(x, y + 2, h - 4, on)
        self._vline(x + w - 1, y + 2, h - 4, on)

        # Corner pixels to suggest radius=2
        # Top-left
        self._pixel(x + 1, y, on)
        self._pixel(x, y + 1, on)
        # Top-right
        self._pixel(x + w - 2, y, on)
        self._pixel(x + w - 1, y + 1, on)
        # Bottom-left
        self._pixel(x, y + h - 2, on)
        self._pixel(x + 1, y + h - 1, on)
        # Bottom-right
        self._pixel(x + w - 1, y + h - 2, on)
        self._pixel(x + w - 2, y + h - 1, on)

    # ----------------------------
    # Public API
    # ----------------------------
    def clear(self, x=None, y=None, w=None, h=None):
        """
        Clears the full bar area (including border).
        Uses stored geometry if args omitted.
        """
        x = self.x if x is None else int(x)
        y = self.y if y is None else int(y)
        w = self.width if w is None else int(w)
        h = self.height if h is None else int(h)

        self._fill_rect(x, y, w, h, on=False)

    def draw(self, x=None, y=None, w=None, h=None, p=None,
             outline=True, clear_bg=True,
             mode="left", indicator_p=None, indicator_ps=None):
        """
        Draw the bar.

        indicator_p:
          - single tick position in [0..1]

        indicator_ps:
          - optional list/tuple of tick positions in [0..1]
          - drawn AFTER fill/outline so they remain visible
        """
        fb = self._fb()
        if fb is None:
            return

        x = self.x if x is None else int(x)
        y = self.y if y is None else int(y)
        w = self.width if w is None else int(w)
        h = self.height if h is None else int(h)

        if p is None:
            p = 0.0
        try:
            p = float(p)
        except Exception:
            p = 0.0
        p = self._clamp(p, 0.0, 1.0)

        if clear_bg:
            self._fill_rect(x, y, w, h, on=False)

        if outline:
            self._round_rect_outline(x, y, w, h, on=True)

        inner_x, inner_y, inner_w, inner_h = self._inner_geom(x, y, w, h)

        fill_w = int(inner_w * p)
        if fill_w <= 0:
            fill_w = 0

        if mode == "center":
            fx = inner_x + (inner_w - fill_w) // 2
        else:
            fx = inner_x

        if fill_w > 0:
            for yy in range(inner_y, inner_y + inner_h):
                for xx in range(fx, fx + fill_w):
                    on = ((xx + yy) & 1) == 0
                    if on:
                        self._pixel(xx, yy, True)

            if mode == "center":
                self._vline(fx, inner_y, inner_h, on=True)
                self._vline(fx + fill_w - 1, inner_y, inner_h, on=True)
            else:
                lead_x = fx + fill_w - 1
                self._vline(lead_x, inner_y, inner_h, on=True)

        # ---- TICKS (draw LAST so they stay visible) ----
        ticks = []

        # single indicator_p
        if indicator_p is not None:
            try:
                ticks.append(float(indicator_p))
            except Exception:
                pass

        # multiple indicator_ps
        if indicator_ps is not None:
            try:
                for ip in indicator_ps:
                    try:
                        ticks.append(float(ip))
                    except Exception:
                        pass
            except Exception:
                pass

        if ticks:
            for ip in ticks:
                ip = self._clamp(ip, 0.0, 1.0)
                ix = inner_x + int((inner_w - 1) * ip)
                self._vline(ix, inner_y, inner_h, on=True)


    def draw_value(self, value, vmin, vmax,
                   x=None, y=None, w=None, h=None,
                   outline=True, clear_bg=True,
                   mode="left",
                   indicator=True):
        """
        Draw bar by mapping a numeric value to [0..1].

        indicator:
          - if True, also draws a tick at the mapped value position.
        """
        try:
            v = float(value)
            lo = float(vmin)
            hi = float(vmax)
        except Exception:
            v = 0.0
            lo = 0.0
            hi = 1.0

        if hi <= lo:
            p = 0.0
        else:
            p = (v - lo) / (hi - lo)
            p = self._clamp(p, 0.0, 1.0)

        ind = p if indicator else None
        self.draw(x=x, y=y, w=w, h=h, p=p,
                  outline=outline, clear_bg=clear_bg,
                  mode=mode,
                  indicator_p=ind)
