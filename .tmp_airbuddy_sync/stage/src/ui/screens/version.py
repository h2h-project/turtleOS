# src/ui/screens/version.py — Version / about screen (Pico / MicroPython safe)
#
# Shows the brand mark centred on the screen — the airBuddy logo + "Know thy
# air..." tagline in airOS, or the animated ASCII turtle in turtleOS — with
# the firmware version number top-left and "by Earthen.io" attribution
# top-right.
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
    FRAME_MS = 500
    REST_MS = 2000
    SWIM_CYCLES = 6

    def __init__(self, oled, turtle_mode=True, turtle_screen_get=None):
        self.oled = oled
        self.turtle_mode = bool(turtle_mode)
        # Callable -> the already-built TurtleWaitingScreen instance, so the
        # (fairly heavy) pre-rendered ASCII frames are reused rather than
        # built a second time.
        self._turtle_screen_get = turtle_screen_get
        self._tick_next = 0

        self._waiting_helper = None
        if not self.turtle_mode:
            try:
                from src.ui.waiting import WaitingScreen
                self._waiting_helper = WaitingScreen()
            except Exception:
                self._waiting_helper = None

    # ------------------------------------------------------------
    # Shared overlay: version number top-left, attribution top-right.
    # ------------------------------------------------------------
    def _draw_header(self, dst, status=None):
        o = self.oled
        writer = getattr(o, "f_small", None)

        try:
            o.f_small.write("v" + str(VERSION_NUM), 0, 1)
        except Exception:
            pass

        # Top-right: attribution.
        if writer is not None:
            attrib = "by Earthen.io"
            try:
                w = int(getattr(o, "width", 128))
                aw, _ = o._text_size(writer, attrib)
                x = max(0, w - int(aw))
            except Exception:
                x = 0
            try:
                writer.write(attrib, x, 1)
            except Exception:
                pass

    # ------------------------------------------------------------
    # airOS: logo + tagline, centred on the full screen.
    # ------------------------------------------------------------
    def _draw_logo(self, status=None):
        o = self.oled
        fb = getattr(o, "oled", None)
        wh = self._waiting_helper
        if fb is None:
            return
        fb.fill(0)

        ow = int(getattr(o, "width", 128))
        oh = int(getattr(o, "height", 64))

        writer = getattr(o, "f_med", None) or getattr(o, "f_small", None)
        line = "Know thy air..."

        lw = lh = 0
        data = None
        if wh is not None:
            try:
                lw, lh, data = wh._get_logo_cached()
            except Exception:
                lw, lh, data = 0, 0, None
        use_logo = bool(wh is not None and lw > 0 and lh > 0 and lw <= ow and lh <= oh and data is not None)

        try:
            _, line_h = writer.size(line) if writer else (0, 8)
        except Exception:
            line_h = 8

        gap = int(getattr(wh, "gap", 6)) if wh is not None else 6
        total_h = (lh + gap + line_h) if use_logo else line_h
        y0 = max(0, (oh - total_h) // 2)

        if use_logo:
            logo_x = max(0, (ow - lw) // 2)
            logo_y = y0 + 10
            ok = False
            try:
                ok = wh._blit_logo_fixed(o, logo_x, logo_y, lw, lh, data)
            except Exception:
                ok = False
            line_y = (logo_y + lh + gap) if ok else y0
        else:
            line_y = y0
        line_y -= 3

        if writer is not None:
            try:
                tw, _ = writer.size(line)
                x = max(0, (ow - int(tw)) // 2)
            except Exception:
                x = 0
            try:
                writer.write(line, x, int(line_y))
            except Exception:
                pass

        self._draw_header(fb, status)
        fb.show()

    def _show_live_logo(self, btn, tick_fn, status):
        self._draw_logo(status)

        tick_next = time.ticks_add(time.ticks_ms(), 500)
        while True:
            now = time.ticks_ms()
            if tick_fn is not None and time.ticks_diff(now, tick_next) >= 0:
                try:
                    tick_fn()
                except Exception:
                    pass
                tick_next = time.ticks_add(now, 500)

            if btn is not None:
                try:
                    action = btn.poll_action()
                except Exception:
                    action = None
                if action is not None:
                    return action

            time.sleep_ms(self.POLL_MS)

    # ------------------------------------------------------------
    # turtleOS: reuse the waiting screen's pre-rendered ASCII turtle frames.
    # ------------------------------------------------------------
    def _turtle_frames(self):
        if self._turtle_screen_get is None:
            return None, None
        try:
            scr = self._turtle_screen_get()
        except Exception:
            scr = None
        if scr is None:
            return None, None
        return getattr(scr, "_swim", None), getattr(scr, "_rest", None)

    def _draw_turtle_frame(self, frame, status=None):
        fb, _, x, y = frame
        dst = self.oled.oled
        dst.fill(0)
        dst.blit(fb, x, y)
        self._draw_header(dst, status)
        dst.show()

    def _draw_turtle_text_fallback(self, status=None):
        o = self.oled
        fb = getattr(o, "oled", None)
        if fb is None:
            return
        fb.fill(0)
        try:
            o.draw_centered(o.f_arvo20, "turtleOS", o.height // 2 - 8)
        except Exception:
            pass
        self._draw_header(fb, status)
        fb.show()

    def _show_live_turtle(self, btn, tick_fn, status):
        swim, rest = self._turtle_frames()
        if not swim or not rest:
            return self._show_live_turtle_fallback(btn, tick_fn, status)

        self._tick_next = time.ticks_ms()

        while True:
            for _ in range(self.SWIM_CYCLES):
                for frame in swim:
                    self._draw_turtle_frame(frame, status)
                    deadline = time.ticks_add(time.ticks_ms(), self.FRAME_MS)
                    action = self._poll(btn, tick_fn, deadline)
                    if action is not None:
                        return action

            self._draw_turtle_frame(rest, status)
            deadline = time.ticks_add(time.ticks_ms(), self.REST_MS)
            action = self._poll(btn, tick_fn, deadline)
            if action is not None:
                return action

    def _show_live_turtle_fallback(self, btn, tick_fn, status):
        self._draw_turtle_text_fallback(status)
        self._tick_next = time.ticks_ms()
        while True:
            action = self._poll(btn, tick_fn, time.ticks_add(time.ticks_ms(), 500))
            if action is not None:
                return action

    def _poll(self, btn, tick_fn, deadline):
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            now = time.ticks_ms()
            if tick_fn is not None and time.ticks_diff(now, self._tick_next) >= 0:
                try:
                    tick_fn()
                except Exception:
                    pass
                self._tick_next = time.ticks_add(now, 500)
            if btn is not None:
                try:
                    action = btn.poll_action()
                except Exception:
                    action = None
                if action is not None:
                    return action
            time.sleep_ms(self.POLL_MS)
        return None

    # ------------------------------------------------------------
    # Public entry point.
    # ------------------------------------------------------------
    def show_live(self, btn=None, tick_fn=None, status=None):
        try:
            btn.reset()
        except Exception:
            pass

        if self.turtle_mode:
            return self._show_live_turtle(btn, tick_fn, status)
        return self._show_live_logo(btn, tick_fn, status)
