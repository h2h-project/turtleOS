# src/ui/screens/version.py — Version / about screen (Pico / MicroPython safe)
#
# Static about screen: the brand mark (turtleOS / airOS, same font as the
# booter screen) near the top, the "Human to Human Hope Delivery" motto
# under it in the small font, and the firmware version number centred
# underneath in the medium font.
#
# Reached via: hold 2s -> Battery screen -> Sleep screen -> single click on
# Sleep -> Version screen -> single click returns to the waiting screen;
# triple click flips turtle_mode (turtleOS <-> airOS) and reboots — the
# caller (sleep_flow in flows.py) acts on the returned "triple" action.

import time

try:
    from src.app.booter import VERSION_NUM
except Exception:
    VERSION_NUM = "?"


class VersionScreen:
    POLL_MS = 25
    TICK_MS = 500

    def __init__(self, oled, turtle_mode=True, turtle_screen_get=None):
        self.oled = oled
        self.turtle_mode = bool(turtle_mode)
        self.brand = "turtleOS" if self.turtle_mode else "airOS"
        # Unused now that the screen no longer reuses the animated turtle
        # frames, kept for call-site compatibility (src/app/main.py passes it).
        self._turtle_screen_get = turtle_screen_get

    # ------------------------------------------------------------
    # Top: brand mark, same font as the booter screen's brand label.
    # ------------------------------------------------------------
    def _draw_brand(self, dst):
        o = self.oled
        writer = getattr(o, "f_arvo20", None) or getattr(o, "f_med", None)
        if writer is None:
            return None
        w = int(getattr(o, "width", 128))
        try:
            bw, bh = o._text_size(writer, self.brand)
        except Exception:
            bw, bh = len(self.brand) * 14, 20
        x = max(0, (w - int(bw)) // 2)
        y = 2
        try:
            writer.write(self.brand, x, y)
        except Exception:
            pass
        return y + bh

    # ------------------------------------------------------------
    # Motto: under the turtleOS brand mark only.
    # ------------------------------------------------------------
    def _draw_motto(self, dst, brand_bottom):
        if not self.turtle_mode or brand_bottom is None:
            return None
        o = self.oled
        writer = getattr(o, "f_small", None)
        if writer is None:
            return brand_bottom
        w = int(getattr(o, "width", 128))
        txt = "Human to Human Hope Delivery"
        try:
            tw, th = o._text_size(writer, txt)
            x = max(0, (w - int(tw)) // 2)
        except Exception:
            th = 7
            x = 0
        y = brand_bottom + 3
        try:
            writer.write(txt, x, y)
        except Exception:
            pass
        return y + th

    # ------------------------------------------------------------
    # Version number: centred, medium font, under the motto.
    # ------------------------------------------------------------
    def _draw_version(self, dst, top):
        if top is None:
            return
        o = self.oled
        writer = getattr(o, "f_med", None)
        if writer is None:
            return
        w = int(getattr(o, "width", 128))
        txt = "v" + str(VERSION_NUM)
        try:
            tw, _ = o._text_size(writer, txt)
            x = max(0, (w - int(tw)) // 2)
        except Exception:
            x = 0
        y = top + 4
        try:
            writer.write(txt, x, y)
        except Exception:
            pass

    def _draw(self, status=None):
        o = self.oled
        fb = getattr(o, "oled", None)
        if fb is None:
            return
        fb.fill(0)
        brand_bottom = self._draw_brand(fb)
        motto_bottom = self._draw_motto(fb, brand_bottom)
        self._draw_version(fb, motto_bottom if motto_bottom is not None else brand_bottom)
        fb.show()

    # ------------------------------------------------------------
    # Public entry point.
    # ------------------------------------------------------------
    def show_live(self, btn=None, tick_fn=None, status=None):
        try:
            btn.reset()
        except Exception:
            pass

        self._draw(status)

        tick_next = time.ticks_add(time.ticks_ms(), self.TICK_MS)
        while True:
            now = time.ticks_ms()
            if tick_fn is not None and time.ticks_diff(now, tick_next) >= 0:
                try:
                    tick_fn()
                except Exception:
                    pass
                tick_next = time.ticks_add(now, self.TICK_MS)

            if btn is not None:
                try:
                    action = btn.poll_action()
                except Exception:
                    action = None
                if action is not None:
                    return action

            time.sleep_ms(self.POLL_MS)
