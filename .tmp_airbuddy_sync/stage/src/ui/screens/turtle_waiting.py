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

_COMPASS_LETTERS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


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

    # Top-left nav cluster: flèche glyph + compass reading ("NE 28°").
    # The flèche is 9px tall so the f_small reading it sits beside gets 1px
    # of clearance above and below; text + degree ring follow to its right.
    _FLECHE_X = 0
    _FLECHE_TEXT_GAP = 3
    _BLE_GAP = 2            # rune -> flèche
    _READING_Y = 1

    # Space to reserve left of the mission text for the target glyph
    # (7px glyph + 4px gap).
    _TARGET_GAP = 11

    # Gap between the target glyph and the mission text that follows it.
    _TARGET_TEXT_GAP = 4

    # Downward nudge for the target glyph so it sits on the mission text's
    # baseline rather than riding high on the f_small row.
    _TARGET_DY = 1

    # Battery icon: bottom-right corner, flush to the right edge. _BATT_DY
    # nudges it down onto the mission/current-draw baseline; _BATT_TEXT_GAP is
    # the space between it and the current-draw text on its left.
    _BATT_DY = 1
    _BATT_TEXT_GAP = 3

    # Gap between the battery current number and the charge/discharge marker
    # glyph on its right (bolt when charging, minus bar when discharging).
    _CURR_MARK_GAP = 2

    # Bottom-left label config is re-read from flash at most this often — the
    # overlay redraws at ~1 Hz and load_config() is a file read + JSON parse.
    _CFG_TTL_MS = 1500

    def __init__(self, oled, nav_get=None, mission_get=None, battery_get=None, current_get=None):
        self.oled = oled
        self._nav_get = nav_get        # callable -> NavController or None
        self._mission_get = mission_get  # callable -> mission name str or None
        self._battery_get = battery_get  # callable -> bus voltage (float) or None
        self._current_get = current_get  # callable -> INA219 current in mA (float) or None
        self._cfg_cache = None         # load_config() result, refreshed on _CFG_TTL_MS
        self._cfg_cache_ms = 0
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

    def _cfg(self):
        """Config dict, cached for _CFG_TTL_MS so the 1 Hz overlay isn't
        parsing config.json every frame."""
        now = time.ticks_ms()
        if self._cfg_cache is not None and \
                time.ticks_diff(now, self._cfg_cache_ms) < self._CFG_TTL_MS:
            return self._cfg_cache
        try:
            from config import load_config
            self._cfg_cache = load_config() or {}
        except Exception:
            self._cfg_cache = self._cfg_cache or {}
        self._cfg_cache_ms = now
        return self._cfg_cache

    def _journey_active(self):
        try:
            from src.app import journey
            return bool(journey.active())
        except Exception:
            return False

    def _operator_label(self):
        """Bottom-left label while an operator journey is open AND a custom
        destination is set: the operator's short name ("SET" by default),
        shown in place of the grand-mission name. None otherwise."""
        if not self._journey_active():
            return None
        cfg = self._cfg()
        if not cfg or not cfg.get("set_destination"):
            return None
        sn = cfg.get("set_short_name")
        return (str(sn).strip().upper() or "SET") if sn else "SET"

    def _battery_volts(self):
        if self._battery_get is None:
            return None
        try:
            return self._battery_get()
        except Exception:
            return None

    def _battery_current_ma(self):
        if self._current_get is None:
            return None
        try:
            return self._current_get()
        except Exception:
            return None

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
        battery current draw/charge then battery icon bottom-right."""
        o = self.oled
        w = o.width
        h = o.height
        ty = h - 8                     # bottom text row (f_small is 7 px)

        nav = self._nav()

        # Top-left: navigation flèche (machine state) + compass reading.
        # Hollow flèche while acquiring, filled once the mission is under way;
        # the heading ("NE 28°") sits on the f_small row to its right, with
        # the degree ring drawn as a glyph after the number.
        ry = self._READING_Y

        # Bluetooth rune at the far left while Bluetooth is on (blinks while
        # advertising); the flèche and heading shift right to make room. The
        # space stays reserved during a blink-off frame so nothing jitters.
        fleche_x = self._FLECHE_X
        if _ch is not None:
            try:
                if _ch.ble_on():
                    from src.ui.glyphs import draw_ble_rune, BLE_RUNE_W
                    if _ch.ble_visible():
                        # caps rows: one below the font's blank top row
                        draw_ble_rune(dst, self._FLECHE_X, ry + 1)
                    fleche_x = self._FLECHE_X + BLE_RUNE_W + self._BLE_GAP
            except Exception:
                pass

        try:
            from src.nav.state_machine import is_mission_active
            from src.ui.glyphs import draw_nav_fleche, NAV_FLECHE_W
            draw_nav_fleche(dst, fleche_x, ry, filled=is_mission_active())
            fleche_w = NAV_FLECHE_W
        except Exception:
            fleche_w = 11

        hdg = None
        if nav is not None:
            try:
                hdg = nav.heading_deg()
            except Exception:
                hdg = None

        rx = fleche_x + fleche_w + self._FLECHE_TEXT_GAP
        if hdg is None:
            o.f_small.write("--", rx, ry)
        else:
            hdg = float(hdg) % 360.0
            letter = _COMPASS_LETTERS[int((hdg + 22.5) / 45.0) % 8]
            txt = "{} {}".format(letter, int(hdg))
            o.f_small.write(txt, rx, ry)
            try:
                tw, _ = o._text_size(o.f_small, txt)
            except Exception:
                tw = len(txt) * 5
            try:
                from src.ui.glyphs import draw_degree_sm
                draw_degree_sm(dst, rx + tw + 1, ry + 1)
            except Exception:
                pass

        # Bottom-right corner: battery charge-level icon, flush to the right edge.
        try:
            from src.ui.glyphs import BATT_W as _batt_w
        except Exception:
            _batt_w = 12
        batt_x = w - _batt_w
        volts = self._battery_volts()
        try:
            if volts is None:
                from src.ui.glyphs import draw_battery
                draw_battery(dst, batt_x, ty + self._BATT_DY, no_battery=True)
            else:
                from src.ui.glyphs import draw_battery_level, battery_display_bands
                bands, _status = battery_display_bands(volts)
                draw_battery_level(dst, batt_x, ty + self._BATT_DY, bands_filled=bands)
        except Exception:
            pass

        # Battery current draw/charge. INA219 raw current_ma() sign on this rig:
        # negative = charging (current flowing into the pack), positive =
        # discharging (the XIAO drawing from it). Layout from the right edge:
        #   [ number "104mA" ] gap [ marker ] gap [ battery icon ]
        # The marker is a solid lightning bolt when charging and a minus bar
        # when discharging; both glyphs are BOLT_W wide, so the marker slot is
        # fixed width and the number's position never shifts when the sign
        # flips. No decimal either way. right_w tracks the width consumed from
        # the right edge so the bottom-left mission text can steer clear.
        try:
            from src.ui.glyphs import BOLT_W as _mark_w
        except Exception:
            _mark_w = 5
        mark_x = batt_x - self._BATT_TEXT_GAP - _mark_w
        current_ma = self._battery_current_ma()
        charging = current_ma is not None and current_ma <= 0
        if current_ma is None:
            txt = "------"
            marker = None
        else:
            txt = "{:d}mA".format(int(round(abs(current_ma))))
            marker = "bolt" if charging else "minus"
        try:
            tw, _ = o._text_size(o.f_small, txt)
        except Exception:
            tw = len(txt) * 5
        tx = mark_x - self._CURR_MARK_GAP - tw
        o.f_small.write(txt, tx, ty)
        # Marker glyph flush to the bottom screen line (y = h - glyph height).
        if marker == "bolt":
            try:
                from src.ui.glyphs import draw_bolt, BOLT_H
                draw_bolt(dst, mark_x, h - BOLT_H)
            except Exception:
                pass
        elif marker == "minus":
            try:
                from src.ui.glyphs import draw_minus, MINUS_H
                draw_minus(dst, mark_x, h - MINUS_H)
            except Exception:
                pass
        right_w = w - tx

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
            # Operator journey with a custom destination -> "SET" (or the
            # operator's short name); otherwise the grand-mission name.
            name = self._operator_label()
            if name is None:
                m = self._mission()
                name = m.upper() if m is not None else None
            if name is not None:
                # Uppercase so the mission reads at the same visual size as the
                # other all-caps bottom text (heading / SWEEP).
                # Reserve room on the left for the target glyph + a 5px gap.
                bl_txt = self._fit(name, w - right_w - 4 - self._TARGET_GAP)
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
        _last_ble = None

        def _btn():
            # Sampled between every expensive step below, not just once per
            # iteration: a click is only ~100 ms of debounced level change, and
            # a frame draw plus a telemetry/nav tick back to back is long enough
            # to step over one entirely — which loses the 2nd or 3rd click of a
            # triple and turns it into a single or double.
            if btn is None:
                return None
            try:
                return btn.poll_action()
            except Exception:
                return None

        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            action = _btn()
            if action is not None:
                return action

            now = time.ticks_ms()
            if tick_fn is not None and time.ticks_diff(now, tick_state[0]) >= 0:
                try:
                    tick_fn()
                except Exception:
                    pass
                tick_state[0] = time.ticks_add(now, 500)
                action = _btn()
                if action is not None:
                    return action
            # Redraw at 1 Hz, or immediately whenever the morse circle state changes
            # (allows 50 ms morse symbols to be visible on the OLED).
            try:
                _cur_morse = _ch._api_morse_circle[0] if _ch else 0
            except Exception:
                _cur_morse = 0
            # ...and whenever a Bluetooth indicator should appear, vanish or
            # blink (1 s phase while advertising) — redraws on each flip.
            try:
                _cur_ble = (_ch.ble_on(), _ch.ble_visible(now)) if _ch else None
            except Exception:
                _cur_ble = None
            if (time.ticks_diff(now, _overlay_next) >= 0 or _cur_morse != _last_morse
                    or _cur_ble != _last_ble):
                try:
                    st = idle_state[1] if idle_state else None
                    self._draw(self._cur, st)
                except Exception:
                    pass
                _overlay_next = time.ticks_add(now, 1000)
                _last_morse = _cur_morse
                _last_ble = _cur_ble
                action = _btn()
                if action is not None:
                    return action
            if on_idle is not None and idle_state is not None:
                if time.ticks_diff(now, idle_state[0]) >= 0:
                    try:
                        ret = on_idle(now)
                        if isinstance(ret, dict):
                            idle_state[1].update(ret)
                    except Exception:
                        pass
                    idle_state[0] = time.ticks_add(now, idle_state[2])
                    action = _btn()
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
