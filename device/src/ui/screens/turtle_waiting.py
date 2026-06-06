import time
import framebuf

try:
    from src.ui import connection_header as _ch
except Exception:
    _ch = None

_HEADER_H = 10  # pixels reserved at top for connectivity icon strip

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
    Render ASCII art scaled down 2pt (5×6 px/char vs native 8×8) into a
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

    # Step 3: nearest-neighbour scale to 5×8 px/char.
    dst_w = n_cols * 5
    dst_h = n_rows * 8
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

    avail_h = display_h - _HEADER_H
    x_off = (display_w - dst_w) // 2
    y_off = _HEADER_H + (avail_h - dst_h) // 2
    return dst_fb, dst_buf, x_off, y_off


class TurtleWaitingScreen:
    POLL_MS = 25
    FRAME_MS = 500
    REST_MS = 2000
    SWIM_CYCLES = 6

    def __init__(self, oled):
        self.oled = oled
        w, h = oled.width, oled.height
        f1_fb,  f1_buf,  f1_x,  f1_y  = _prerender(_TURTLE_1,    w, h)
        f2_fb,  f2_buf,  f2_x,  f2_y  = _prerender(_TURTLE_2,    w, h)
        fr_fb,  fr_buf,  fr_x,  fr_y  = _prerender(_TURTLE_REST,  w, h)
        self._f1   = (f1_fb,  f1_buf,  f1_x,  f1_y)
        self._f2   = (f2_fb,  f2_buf,  f2_x,  f2_y)
        self._rest = (fr_fb,  fr_buf,  fr_x,  fr_y)
        self._swim = (self._f1, self._f2)

    def _draw(self, frame, status=None):
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
        dst.show()

    def _poll(self, btn, tick_fn, tick_state, deadline, on_idle=None, idle_state=None):
        # idle_state: [next_ms, live_status_dict, interval_ms]
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            now = time.ticks_ms()
            if tick_fn is not None and time.ticks_diff(now, tick_state[0]) >= 0:
                try:
                    tick_fn()
                except Exception:
                    pass
                tick_state[0] = time.ticks_add(now, 500)
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
