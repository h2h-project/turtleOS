# src/ui/waiting.py  (MicroPython / Pico-safe)
#
# WAITING SCREEN
# - airBuddy logo + tagline
# - optional 3-dot animation
# - top-right status icons: GPS  API  WIFI
# - background idle hook (telemetry tick lives here)
#
# PATCH (Feb 2026 - stability + UI refresh):
# - FIX: OLED refresh must call oled.oled.show() (NOT fb.show()).
# - Reduce on_idle polling frequency to avoid RAM churn.
# - Stop REPL spam (log only on entry; optional debug toggle).
# - Always draw GPS icon (empty when not detected).
# - API heartbeat only when api_ok=True (and WiFi ok), sending forces filled.
#
# PATCH (Mar 2026 - connection_header sync):
# - Do NOT force api_connected=False into connection_header.draw(),
#   because that overrides the module-level API cache updated by
#   telemetry_scheduler via connection_header.set_api_ok(True/False).
# - Only pass api_connected=True when we have an explicit positive state.
#   Otherwise pass None so connection_header can use its cached value.
# - When on_idle returns api_ok, sync that value into connection_header too.

import time
import gc
from src.ui import logo_airbuddy
from src.ui import connection_header
from src.ui.connection_header import GPS_NONE, GPS_INIT, GPS_FIXED  # noqa: F401


def _to_gps_state(val):
    """Convert a bool or int GPS value to GPS_NONE/GPS_INIT/GPS_FIXED int."""
    if isinstance(val, int):
        return val
    return GPS_FIXED if bool(val) else GPS_NONE


class WaitingScreen:
    def __init__(self, flip_x=False, flip_y=True, gap=6, logo_drop_px=10):
        self.flip_x = bool(flip_x)
        self.flip_y = bool(flip_y)

        self.gap = int(gap)
        self.logo_drop_px = int(logo_drop_px)

        self._start_ms = None

        self._logo_lw = None
        self._logo_lh = None
        self._logo_data = None

        self.logo_y_offset_px = -2
        self.line_y_offset_px = -6

        self.cluster_right_inset_px = 1
        self.icon_gap_px = 4
        self.icon_y = 1

        self.dots_period_ms = 1000
        self._last_dot_step = None

        # background idle scheduler
        self._idle_next_ms = 0

        # live status cache (updated by on_idle return)
        self._wifi_ok = False
        self._gps_on = GPS_NONE   # int: GPS_NONE / GPS_INIT / GPS_FIXED
        self._api_ok = False

        # optional "sending" pulse
        self._api_sending_until_ms = 0
        self.api_sending_hold_ms = 2000

        # last-render state
        self._last_wifi_ok = None
        self._last_gps_on = None
        self._last_api_ok = None
        self._last_api_sending = None
        self._last_heartbeat_phase = None
        self._anim_frozen = False

        # ---- DEBUG ----
        # Keep False in normal runs; turn on temporarily if diagnosing.
        self.log_status_checks = False

    # ============================================================
    # PUBLIC API (render once)
    # ============================================================
    def show(
            self,
            oled,
            line="Know thy air...",
            animate=False,
            period_ms=1000,
            *,
            wifi_ok=False,
            gps_on=False,
            api_ok=False,
            api_sending=False,
    ):
        self._wifi_ok = bool(wifi_ok)
        self._gps_on = _to_gps_state(gps_on)
        self._api_ok = bool(api_ok)

        # Keep shared connection_header API cache in sync with explicit state
        try:
            connection_header.set_api_ok(self._api_ok)
        except Exception:
            pass

        if api_sending:
            now = self._now_ms()
            self._api_sending_until_ms = self._ticks_add(now, int(self.api_sending_hold_ms))

        self.render(
            oled,
            line=line,
            animate=animate,
            period_ms=period_ms,
            wifi_ok=self._wifi_ok,
            gps_on=self._gps_on,
            api_ok=self._api_ok,
            api_sending=self._is_api_sending(self._now_ms()),
        )

    # ============================================================
    # PUBLIC API (HOLD until click)
    # ============================================================
    def show_live(
            self,
            oled,
            btn,
            line="Know thy air...",
            animate=True,
            period_ms=None,
            *,
            wifi_ok=False,
            gps_on=False,
            api_ok=False,
            poll_ms=25,
            flush_ms=220,
            on_idle=None,
            idle_every_ms=4000,
    ):
        if period_ms is None:
            period_ms = int(self.dots_period_ms)

        # seed live status
        self._wifi_ok = bool(wifi_ok)
        self._gps_on = _to_gps_state(gps_on)
        self._api_ok = bool(api_ok)

        # Keep shared connection_header API cache in sync with initial state
        try:
            connection_header.set_api_ok(self._api_ok)
        except Exception:
            pass

        # gc schedule: collect at a low rate rather than every render
        _gc_every_ms = 15000
        _gc_next_ms = self._ticks_add(self._now_ms(), _gc_every_ms)

        # initial render
        self.render(
            oled,
            line=line,
            animate=bool(animate),
            period_ms=period_ms,
            wifi_ok=self._wifi_ok,
            gps_on=self._gps_on,
            api_ok=self._api_ok,
            api_sending=self._is_api_sending(self._now_ms()),
        )

        self._last_dot_step = self._anim_step(period_ms) if animate else None

        if btn is None:
            return None

        self._anim_frozen = False

        try:
            btn.reset()
        except Exception:
            pass

        # short flush of stale clicks
        try:
            t0 = time.ticks_ms()
            while time.ticks_diff(time.ticks_ms(), t0) < int(flush_ms):
                try:
                    btn.poll_action()
                except Exception:
                    pass
                time.sleep_ms(int(poll_ms))
        except Exception:
            pass

        # schedule first background call
        now = self._now_ms()
        self._idle_next_ms = self._ticks_add(now, int(idle_every_ms))

        # --------------------------------------------------------
        # DO ONE STATUS CHECK ON ENTRY (and log once if enabled)
        # --------------------------------------------------------
        if on_idle is not None:
            try:
                ret = on_idle(now)
                if self.log_status_checks:
                    print("[WAITING] status check (entry) ->", ret)
                self._apply_idle_ret(ret, now)
            except Exception as e:
                if self.log_status_checks:
                    print("[WAITING] status check (entry) ERROR:", repr(e))

        # force redraw after entry check
        api_sending = self._is_api_sending(now)
        self.render(
            oled,
            line=line,
            animate=bool(animate),
            period_ms=period_ms,
            wifi_ok=self._wifi_ok,
            gps_on=self._gps_on,
            api_ok=self._api_ok,
            api_sending=api_sending,
        )
        _api_conn = bool(self._wifi_ok) and bool(self._api_ok)
        self._remember_last(api_sending, self._heartbeat_phase(now, _api_conn, api_sending))

        # ========================================================
        # WAIT LOOP
        # ========================================================
        while True:
            now = self._now_ms()

            # ----------------------------------------------------
            # 1) Background idle hook (RARE)
            # ----------------------------------------------------
            if on_idle is not None:
                try:
                    if self._ticks_diff(now, self._idle_next_ms) >= 0:
                        ret = on_idle(now)
                        if self.log_status_checks:
                            print("[WAITING] status check ->", ret)

                        self._apply_idle_ret(ret, now)
                        self._idle_next_ms = self._ticks_add(now, int(idle_every_ms))
                except Exception:
                    pass

            # Low-rate gc
            if self._ticks_diff(now, _gc_next_ms) >= 0:
                try:
                    gc.collect()
                except Exception:
                    pass
                _gc_next_ms = self._ticks_add(now, _gc_every_ms)

            api_sending = self._is_api_sending(now)
            api_connected = bool(self._wifi_ok) and bool(self._api_ok)
            hb_phase = self._heartbeat_phase(now, api_connected, api_sending)

            # Freeze animation on first press so click counting is uninterrupted
            if not self._anim_frozen:
                try:
                    if btn.is_interacting():
                        self._anim_frozen = True
                except Exception:
                    pass

            # ----------------------------------------------------
            # 2) Redraw only if needed
            # ----------------------------------------------------
            redraw = False

            if animate:
                try:
                    step = self._anim_step(period_ms)
                except Exception:
                    step = None
                if (step is not None) and (step != self._last_dot_step):
                    self._last_dot_step = step
                    redraw = True

            if (self._wifi_ok != self._last_wifi_ok or
                    self._gps_on != self._last_gps_on or
                    self._api_ok != self._last_api_ok or
                    api_sending != self._last_api_sending or
                    (not self._anim_frozen and hb_phase != self._last_heartbeat_phase)):
                redraw = True

            if redraw:
                self.render(
                    oled,
                    line=line,
                    animate=bool(animate),
                    period_ms=period_ms,
                    wifi_ok=self._wifi_ok,
                    gps_on=self._gps_on,
                    api_ok=self._api_ok,
                    api_sending=api_sending,
                )
                self._remember_last(api_sending, hb_phase)

            # ----------------------------------------------------
            # 3) Poll button
            # ----------------------------------------------------
            try:
                action = btn.poll_action()
            except Exception:
                action = None

            if action is not None:
                return action

            time.sleep_ms(int(poll_ms))

    # ============================================================
    # Helpers
    # ============================================================
    def _heartbeat_phase(self, now_ms, api_connected, api_sending):
        """
        Return the current animation phase bucket (int) or None when not connected.
        A phase change is the only trigger for a redraw, so bucket boundaries
        must align exactly with frame transitions in _api_heartbeat_on().

        Sending  (2 s cycle) : 4 buckets × 500 ms each
        Idle     (8.2 s cycle): bucket 0 = 7 s solid, buckets 1-6 = 200 ms each
        """
        if not api_connected:
            return None
        if api_sending:
            t = now_ms % 2000
            if t < 500:
                return 0
            if t < 1000:
                return 1
            if t < 1500:
                return 2
            return 3
        else:
            t = now_ms % 8200
            if t < 7000:
                return 0
            return 1 + ((t - 7000) // 200)

    def _remember_last(self, api_sending, heartbeat_phase=None):
        self._last_wifi_ok = self._wifi_ok
        self._last_gps_on = self._gps_on
        self._last_api_ok = self._api_ok
        self._last_api_sending = api_sending
        self._last_heartbeat_phase = heartbeat_phase

    def _apply_idle_ret(self, ret, now_ms):
        if not isinstance(ret, dict):
            return

        if "wifi_ok" in ret:
            self._wifi_ok = bool(ret.get("wifi_ok"))

        if "gps_state" in ret:
            self._gps_on = _to_gps_state(ret.get("gps_state"))
        elif "gps_on" in ret:
            self._gps_on = _to_gps_state(ret.get("gps_on"))

        if "api_ok" in ret:
            self._api_ok = bool(ret.get("api_ok"))
            try:
                connection_header.set_api_ok(self._api_ok)
            except Exception:
                pass

        # hold pulse
        if ret.get("api_sending", False):
            self._api_sending_until_ms = self._ticks_add(now_ms, int(self.api_sending_hold_ms))

    def _now_ms(self):
        try:
            return time.ticks_ms()
        except Exception:
            return int(time.time() * 1000)

    def _ticks_add(self, a, b):
        try:
            return time.ticks_add(a, b)
        except Exception:
            return a + b

    def _ticks_diff(self, a, b):
        try:
            return time.ticks_diff(a, b)
        except Exception:
            return a - b

    def _elapsed_ms(self, now_ms):
        if self._start_ms is None:
            self._start_ms = now_ms
            return 0
        return self._ticks_diff(now_ms, self._start_ms)

    def _anim_step(self, period_ms=1000):
        elapsed = self._elapsed_ms(self._now_ms())
        p = int(period_ms) or 1000
        return int(elapsed // p) % 4

    def _animated_line(self, base, period_ms=1000):
        step = self._anim_step(period_ms)
        if step == 0:
            return base
        if step == 1:
            return base + "."
        if step == 2:
            return base + ".."
        return base + "..."

    def _is_api_sending(self, now_ms):
        return self._ticks_diff(now_ms, self._api_sending_until_ms) < 0

    # ============================================================
    # LOGO
    # ============================================================
    def _get_logo_cached(self):
        if self._logo_lw is not None:
            return self._logo_lw, self._logo_lh, self._logo_data

        lw = int(getattr(logo_airbuddy, "WIDTH", 0) or 0)
        lh = int(getattr(logo_airbuddy, "HEIGHT", 0) or 0)
        data = getattr(logo_airbuddy, "DATA", None)

        if (lw <= 0) or (lh <= 0) or (data is None):
            self._logo_lw, self._logo_lh, self._logo_data = 0, 0, None
            return 0, 0, None

        if not isinstance(data, (bytes, bytearray)):
            try:
                data = bytes(data)
            except Exception:
                self._logo_lw, self._logo_lh, self._logo_data = 0, 0, None
                return 0, 0, None

        self._logo_lw, self._logo_lh, self._logo_data = lw, lh, data
        return lw, lh, data

    def _logo_pixel(self, data, lw, x, y):
        idx = x + (y >> 3) * lw
        b = data[idx]
        return (b >> (y & 7)) & 1

    def _blit_logo_fixed(self, oled, x0, y0, lw, lh, data):
        sw = int(getattr(oled, "width", 128))
        sh = int(getattr(oled, "height", 64))
        fb = getattr(oled, "oled", None)
        if fb is None:
            return False

        for yy in range(lh):
            dy = (lh - 1 - yy) if self.flip_y else yy
            sy = y0 + yy
            if sy < 0 or sy >= sh:
                continue

            for xx in range(lw):
                dx = (lw - 1 - xx) if self.flip_x else xx
                sx = x0 + xx
                if sx < 0 or sx >= sw:
                    continue

                if self._logo_pixel(data, lw, dx, dy):
                    fb.pixel(sx, sy, 1)

        return True

    # ============================================================
    # Status icons
    # ============================================================
    def _draw_status_icons(self, oled, wifi_ok=False, gps_on=GPS_NONE, api_ok=False, api_sending=False):
        fb = getattr(oled, "oled", None)
        if fb is None:
            return

        # IMPORTANT:
        # Only pass explicit True when we positively know API is up.
        # Otherwise pass None so connection_header can use its shared cache.
        # This prevents waiting.py from overwriting a good cached API state
        # with a stale False.
        api_connected = True if (bool(wifi_ok) and bool(api_ok)) else None

        connection_header.draw(
            fb,
            oled_width=int(getattr(oled, "width", 128)),
            gps_state=_to_gps_state(gps_on),
            api_connected=api_connected,
            wifi_ok=bool(wifi_ok),
            api_sending=bool(api_sending),
            icon_y=int(self.icon_y),
            right_inset=int(self.cluster_right_inset_px),
            gap=int(self.icon_gap_px),
        )

    # ============================================================
    # Full render
    # ============================================================
    def render(
            self,
            oled,
            line="Know thy air...",
            animate=False,
            period_ms=1000,
            *,
            wifi_ok=False,
            gps_on=False,
            api_ok=False,
            api_sending=False
    ):
        fb = getattr(oled, "oled", None)
        if fb is None:
            return

        ow = int(getattr(oled, "width", 128))
        oh = int(getattr(oled, "height", 64))

        fb.fill(0)

        writer = getattr(oled, "f_med", None) or getattr(oled, "f_small", None)
        if writer is None:
            return

        self._draw_status_icons(oled, wifi_ok, gps_on, api_ok, api_sending)

        base = (line or "").rstrip().rstrip(". ")
        p = int(period_ms) or 1000
        line_to_draw = base + "..." if not animate else self._animated_line(base, p)

        lw, lh, data = self._get_logo_cached()
        use_logo = (lw > 0 and lh > 0 and lw <= ow and lh <= oh and data is not None)

        try:
            _, line_h = writer.size(line_to_draw)
        except Exception:
            line_h = 8

        total_h = (lh + self.gap + line_h) if use_logo else line_h
        y_centered = max(0, (oh - total_h) // 2)

        logo_y = y_centered + self.logo_drop_px + int(self.logo_y_offset_px)

        if use_logo:
            logo_x = max(0, (ow - lw) // 2)
            ok = self._blit_logo_fixed(oled, logo_x, logo_y, lw, lh, data)
            line_y = (logo_y + lh + self.gap) if ok else logo_y
        else:
            line_y = logo_y

        line_y = int(line_y) + int(self.line_y_offset_px)

        try:
            tw, _ = writer.size(line_to_draw)
            x = max(0, (ow - int(tw)) // 2)
        except Exception:
            x = 0

        try:
            writer.write(line_to_draw, x, int(line_y))
        except Exception:
            pass

        # CRITICAL FIX: show must be called on the OLED driver, not framebuffer
        try:
            oled.oled.show()
        except Exception:
            try:
                fb.show()
            except Exception:
                pass