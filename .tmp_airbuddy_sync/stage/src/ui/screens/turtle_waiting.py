import time
import framebuf

try:
    from src.ui import connection_header as _ch
except Exception:
    _ch = None

_HEADER_H = 10  # pixels reserved at top for connectivity icon strip
_FOOTER_H = 8   # pixels reserved at bottom for the heading/mission text row (f_small)

_TURTLE_1 = (
    "  _______    ___",
    "/         \\ |  0|",
    "|         |/ ___-",
    "|___________/",
    " |__| |__|",
)

_TURTLE_2 = (
    "  _______   ___",
    "/         \\|  0|",
    "|         || __-",
    "|__________/",
    "  |__| |__|",
)

_TURTLE_REST = (
    "  _______    ___",
    "/         \\ |  0|",
    "|         |/ __\\|",
    "|___________/",
    " |__| |__|",
)

_SWIM_FRAMES = (_TURTLE_1, _TURTLE_2)


def _prerender(lines, display_w, display_h):
    """
    Render ASCII art scaled down (≈4.4×7 px/char vs native 8×8) into a
    MONO_VLSB FrameBuffer ready to blit.
    Returns (FrameBuffer, bytearray, x_offset, y_offset).
    bytearray must stay alive alongside FrameBuffer.
    """
    import gc
    n_cols = max(len(ln) for ln in lines)
    n_rows = len(lines)
    src_w = n_cols * 8
    src_h = n_rows * 8

    # Step 1: render text at native 8×8 into MONO_HLSB (easy pixel reads).
    src_buf = bytearray(((src_w + 7) // 8) * src_h)
    src_fb = framebuf.FrameBuffer(src_buf, src_w, src_h, framebuf.MONO_HLSB)
    for i, ln in enumerate(lines):
        src_fb.text(ln, 0, i * 8, 1)

    # Step 2: strip col 7 of each char → 7×8 px/char intermediate (MONO_HLSB).
    int_w = n_cols * 7
    int_h = src_h
    int_buf = bytearray(((int_w + 7) // 8) * int_h)
    int_fb = framebuf.FrameBuffer(int_buf, int_w, int_h, framebuf.MONO_HLSB)
    for ci in range(n_cols):
        for col in range(7):
            src_x = ci * 8 + col
            dst_x = ci * 7 + col
            for row in range(src_h):
                if src_fb.pixel(src_x, row):
                    int_fb.pixel(dst_x, row, 1)

    del src_buf, src_fb
    gc.collect()

    # Step 3: nearest-neighbour scale. Was 5×8 px/char; reduced ~12% to make
    # room for the nav overlays in the screen corners.
    dst_w = n_cols * 7 * 5 // 8       # ≈4.4 px/char wide
    dst_h = n_rows * 7                # 7 px/char tall
    dst_buf = bytearray(dst_w * ((dst_h + 7) // 8))
    dst_fb = framebuf.FrameBuffer(dst_buf, dst_w, dst_h, framebuf.MONO_VLSB)
    for dy in range(dst_h):
        sy = dy * int_h // dst_h
        for dx in range(dst_w):
            sx = dx * int_w // dst_w
            if int_fb.pixel(sx, sy):
                dst_fb.pixel(dx, dy, 1)

    del int_buf, int_fb
    gc.collect()

    # Centre the turtle in the band between the bottom of the connection
    # header and the top of the bottom text row (heading / mission footer).
    avail_h = display_h - _HEADER_H - _FOOTER_H
    x_off = (display_w - dst_w) // 2
    y_off = _HEADER_H + (avail_h - dst_h) // 2
    return dst_fb, dst_buf, x_off, y_off


class TurtleWaitingScreen:
    POLL_MS = 25
    FRAME_MS = 500
    REST_MS = 2000
    SWIM_CYCLES = 6

    _LETTERS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

    # Space to reserve left of the mission text for the target glyph
    # (7px glyph + 4px gap).
    _TARGET_GAP = 11

    # Gap between the target glyph and the mission text that follows it.
    _TARGET_TEXT_GAP = 4

    # Downward nudge for the target glyph so it sits on the mission text's
    # baseline rather than riding high on the f_small row.
    _TARGET_DY = 1

    # Battery icon: bottom-right corner, flush to the right edge. _BATT_DY
    # nudges it down onto the mission/heading baseline; _BATT_TEXT_GAP is the
    # space between it and the heading text on its left.
    _BATT_DY = 1
    _BATT_TEXT_GAP = 6

    def __init__(self, oled, nav_get=None, mission_get=None):
        self.oled = oled
        self._nav_get = nav_get        # callable -> NavController or None
        self._mission_get = mission_get  # callable -> mission name str or None
        w, h = oled.width, oled.height
        f1_fb,  f1_buf,  f1_x,  f1_y  = _prerender(_TURTLE_1,    w, h)
        f2_fb,  f2_buf,  f2_x,  f2_y  = _prerender(_TURTLE_2,    w, h)
        fr_fb,  fr_buf,  fr_x,  fr_y  = _prerender(_TURTLE_REST,  w, h)
        self._f1   = (f1_fb,  f1_buf,  f1_x,  f1_y)
        self._f2   = (f2_fb,  f2_buf,  f2_x,  f2_y)
        self._rest = (fr_fb,  fr_buf,  fr_x,  fr_y)
        self._swim = (self._f1, self._f2)
        self._cur = self._rest         # last frame drawn (for overlay refresh)

    def _nav(self):
        if self._nav_get is None:
            return None
        try:
            return self._nav_get()
        except Exception:
            return None

    def _mission(self):
        if self._mission_get is None:
            return None
        try:
            name = self._mission_get()
        except Exception:
            return None
        if not name:
            return None
        return str(name).strip() or None

    def _fit(self, text, max_w):
        """Truncate text (from the end) until it fits within max_w pixels."""
        o = self.oled
        if max_w <= 0:
            return ""
        try:
            tw, _ = o._text_size(o.f_small, text)
        except Exception:
            tw = len(text) * 5
        if tw <= max_w:
            return text
        s = text
        while len(s) > 1:
            s = s[:-1]
            try:
                tw, _ = o._text_size(o.f_small, s)
            except Exception:
                tw = len(s) * 5
            if tw <= max_w:
                break
        return s

    def _overlay(self, dst):
        """Nav status in the screen corners: machine state top-left,
        mission (target glyph + name) or next-sweep countdown bottom-left,
        heading then battery icon bottom-right."""
        o = self.oled
        w = o.width
        h = o.height
        ty = h - 8                     # bottom text row (f_small is 7 px)

        # Top-left: machine state (BOOT / ACQUIRE / SAIL-NAV / ...)
        try:
            from src.nav.state_machine import display_name
            o.f_small.write(display_name(), 0, 1)
        except Exception:
            pass

        nav = self._nav()

        # Bottom-right corner: empty battery outline, flush to the right edge.
        try:
            from src.ui.glyphs import BATT_W as _batt_w
        except Exception:
            _batt_w = 12
        batt_x = w - _batt_w
        try:
            from src.ui.glyphs import draw_battery
            draw_battery(dst, batt_x, ty + self._BATT_DY)
        except Exception:
            pass

        # Compass heading like NE-45° (------ without compass), right-aligned
        # against the battery. Track the total width consumed from the right
        # edge so the bottom-left mission text can steer clear.
        heading_right = batt_x - self._BATT_TEXT_GAP
        heading = None
        if nav is not None:
            try:
                heading = nav.heading_deg()
            except Exception:
                heading = None
        if heading is None:
            txt = "------"
            try:
                tw, _ = o._text_size(o.f_small, txt)
            except Exception:
                tw = 24
            tx = heading_right - tw
            o.f_small.write(txt, tx, ty)
            right_w = w - tx
        else:
            letter = self._LETTERS[int((float(heading) + 22.5) / 45.0) % 8]
            txt = "{}-{}".format(letter, int(heading))
            try:
                tw, _ = o._text_size(o.f_small, txt)
            except Exception:
                tw = len(txt) * 5
            # Text + degree glyph (6px) as one unit, right-aligned on the battery.
            tx = heading_right - (tw + 6)
            o.f_small.write(txt, tx, ty)
            right_w = w - tx
            try:
                from src.ui.glyphs import draw_degree
                draw_degree(dst, tx + tw + 2, ty, r=2)
            except Exception:
                pass

        # Bottom-left: the luff-sweep countdown takes precedence during
        # SAIL-NAV; otherwise show the mission name (prefixed with a target
        # glyph) so an idle turtle still displays where it's headed.
        bl_txt = None
        bl_is_mission = False
        if nav is not None:
            try:
                secs = nav.seconds_to_next_sweep()
            except Exception:
                secs = None
            if secs is not None:
                if nav.sweeping():
                    bl_txt = "SWEEP"
                else:
                    bl_txt = "{}:{:02d}".format(secs // 60, secs % 60)
        if bl_txt is None:
            name = self._mission()
            if name is not None:
                # Uppercase so the mission reads at the same visual size as the
                # other all-caps bottom text (heading / SWEEP).
                # Reserve room on the left for the target glyph + a 5px gap.
                bl_txt = self._fit(name.upper(), w - right_w - 4 - self._TARGET_GAP)
                bl_is_mission = bool(bl_txt)
        if bl_txt:
            if bl_is_mission:
                # Target glyph flush in the bottom-left corner (7px, r=3
                # disc-in-ring, centred on the f_small row); mission text sits
                # to its right, separated by 5px.
                glyph_cx = 3                        # leftmost glyph pixel at 0
                try:
                    from src.ui.glyphs import draw_circle
                    draw_circle(dst, glyph_cx, ty + 3 + self._TARGET_DY,
                                r=3, filled=True, color=1)
                except Exception:
                    pass
                tx = glyph_cx + 3 + self._TARGET_TEXT_GAP   # glyph_right + gap
            else:
                tx = 0                              # sweep countdown: left-aligned
            o.f_small.write(bl_txt, tx, ty)

    def _draw(self, frame, status=None):
        self._cur = frame
        fb, _, x, y = frame
        dst = self.oled.oled
        dst.fill(0)
        dst.blit(fb, x, y)
        if _ch is not None:
            try:
                st = status or {}
                _ch.draw(
                    dst,
                    self.oled.width,
                    gps_state=_ch.get_gps_state(),
                    api_sending=bool(st.get("api_sending", False)),
                    icon_y=1,
                )
            except Exception:
                pass
        try:
            self._overlay(dst)
        except Exception:
            pass
        dst.show()

    def _poll(self, btn, tick_fn, tick_state, deadline, on_idle=None, idle_state=None):
        # idle_state: [next_ms, live_status_dict, interval_ms]
        _overlay_next = time.ticks_add(time.ticks_ms(), 1000)
        _last_morse = 0
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            now = time.ticks_ms()
            if tick_fn is not None and time.ticks_diff(now, tick_state[0]) >= 0:
                try:
                    tick_fn()
                except Exception:
                    pass
                tick_state[0] = time.ticks_add(now, 500)
            # Redraw at 1 Hz, or immediately whenever the morse circle state changes
            # (allows 50 ms morse symbols to be visible on the OLED).
            try:
                _cur_morse = _ch._api_morse_circle[0] if _ch else 0
            except Exception:
                _cur_morse = 0
            if time.ticks_diff(now, _overlay_next) >= 0 or _cur_morse != _last_morse:
                try:
                    st = idle_state[1] if idle_state else None
                    self._draw(self._cur, st)
                except Exception:
                    pass
                _overlay_next = time.ticks_add(now, 1000)
                _last_morse = _cur_morse
            if on_idle is not None and idle_state is not None:
                if time.ticks_diff(now, idle_state[0]) >= 0:
                    try:
                        ret = on_idle(now)
                        if isinstance(ret, dict):
                            idle_state[1].update(ret)
                    except Exception:
                        pass
                    idle_state[0] = time.ticks_add(now, idle_state[2])
            if btn is not None:
                try:
                    action = btn.poll_action()
                except Exception:
                    action = None
                if action is not None:
                    return action
            time.sleep_ms(self.POLL_MS)
        return None

    def show_live(self, btn=None, tick_fn=None, status=None, on_idle=None, idle_every_ms=4000):
        tick_state = [time.ticks_ms()]
        live_status = dict(status or {})
        idle_state = (
            [time.ticks_add(time.ticks_ms(), int(idle_every_ms)), live_status, int(idle_every_ms)]
            if on_idle is not None else None
        )

        while True:
            for _ in range(self.SWIM_CYCLES):
                for frame in self._swim:
                    self._draw(frame, live_status)
                    deadline = time.ticks_add(time.ticks_ms(), self.FRAME_MS)
                    action = self._poll(btn, tick_fn, tick_state, deadline, on_idle, idle_state)
                    if action is not None:
                        return action

            self._draw(self._rest, live_status)
            deadline = time.ticks_add(time.ticks_ms(), self.REST_MS)
            action = self._poll(btn, tick_fn, tick_state, deadline, on_idle, idle_state)
            if action is not None:
                return action
