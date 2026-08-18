# src/ui/screens/version.py — Version / about screen (Pico / MicroPython safe)
#
# Static about screen: "A Human2Human Project" top-centre, the brand mark
# (turtleOS / airOS, same font as the booter screen) centred, and the
# firmware version number + "by Earthen.io" attribution on the bottom row.
#
# Reached via: hold 2s -> Battery screen -> Sleep screen -> single click on
# Sleep -> Version screen -> any click returns to the waiting screen.

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
    # Bottom row: version number bottom-left, attribution bottom-right.
    # ------------------------------------------------------------
    def _draw_footer(self, dst):
        o = self.oled
        writer = getattr(o, "f_small", None)
        if writer is None:
            return

        h = int(getattr(o, "height", 64))
        w = int(getattr(o, "width", 128))
        try:
            _, line_h = o._text_size(writer, "Ag")
        except Exception:
            line_h = 7
        y = h - line_h - 1

        try:
            writer.write("v" + str(VERSION_NUM), 0, y)
        except Exception:
            pass

        attrib = "by Earthen.io"
        try:
            aw, _ = o._text_size(writer, attrib)
            x = max(0, w - int(aw))
        except Exception:
            x = 0
        try:
            writer.write(attrib, x, y)
        except Exception:
            pass

    # ------------------------------------------------------------
    # Top row: project tagline, centred.
    # ------------------------------------------------------------
    def _draw_tagline(self, dst):
        o = self.oled
        writer = getattr(o, "f_small", None)
        if writer is None:
            return
        w = int(getattr(o, "width", 128))
        txt = "A Human2Human Project"
        try:
            tw, _ = o._text_size(writer, txt)
            x = max(0, (w - int(tw)) // 2)
        except Exception:
            x = 0
        try:
            writer.write(txt, x, 1)
        except Exception:
            pass

    # ------------------------------------------------------------
    # Middle: brand mark, same font as the booter screen's brand label.
    # ------------------------------------------------------------
    def _draw_brand(self, dst):
        o = self.oled
        writer = getattr(o, "f_arvo20", None) or getattr(o, "f_med", None)
        if writer is None:
            return
        w = int(getattr(o, "width", 128))
        h = int(getattr(o, "height", 64))
        try:
            bw, bh = o._text_size(writer, self.brand)
        except Exception:
            bw, bh = len(self.brand) * 14, 20
        x = max(0, (w - int(bw)) // 2)
        y = max(0, (h - int(bh)) // 2)
        try:
            writer.write(self.brand, x, y)
        except Exception:
            pass

    def _draw(self, status=None):
        o = self.oled
        fb = getattr(o, "oled", None)
        if fb is None:
            return
        fb.fill(0)
        self._draw_tagline(fb)
        self._draw_brand(fb)
        self._draw_footer(fb)
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
