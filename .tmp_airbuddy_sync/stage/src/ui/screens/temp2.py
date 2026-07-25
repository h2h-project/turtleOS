# src/ui/screens/temp2.py — Temperature screen sourced from SCD41 (Pico / MicroPython safe)
#
# Layout:
# - Top-left:     "Temperature 2" title (f_med, no top margin)
# - Top-right:    connectivity icons (GPS / API / WiFi)
# - Middle:       SCD41 temp large (f_large / arvo24), centered
# - Bottom-left:  SCD41 Humidity "RH 67%"  (f_med)
# - Bottom-right: clock glyph + RTC chip temperature "33°C" (f_med, right-aligned)
#
# Compatibility:
# - show_live(btn=..., air=...)            ✅ (flows.py style)
# - show_live(btn=..., get_reading=...)    ✅
# - show(reading)                         ✅ (one-shot draw)

import time
from src.ui.glyphs import draw_degree, draw_clock, CLOCK_W, CLOCK_H
import src.ui.connection_header as _ch
from src.ui.connection_header import GPS_NONE


class Temp2Screen:
    REFRESH_MS = 4000
    POLL_MS = 25

    def __init__(self, oled, i2c=None, status=None, rtc_info=None):
        self.oled = oled
        self._i2c = i2c
        # Status dict held by reference — always reflects live main-loop state.
        self._status = status if isinstance(status, dict) else {}
        # RTC info dict held by reference — fresh reads are written back so
        # the telemetry scheduler always sees the latest DS3231 temperature.
        self._rtc_info = rtc_info if isinstance(rtc_info, dict) else None
        # Cache last good SCD41 values so the display holds steady when
        # scd41_temp_c / scd41_humidity is None (SCD41 measures every 5 s;
        # refresh is every 4 s — they periodically fall out of sync).
        self._cached_temp_c = None
        self._cached_rh = None

    # -------------------------------------------------
    # Helpers
    # -------------------------------------------------

    @staticmethod
    def _round_1dp(x):
        try:
            return round(float(x), 1)
        except Exception:
            return None

    def _format_temp(self, t):
        if t is None:
            return None
        return "{:.1f}".format(t)

    def _read_rtc_temp(self):
        """
        Read a fresh temperature directly from the DS3231 chip.
        probe=False skips the bus scan on every refresh call.
        Writes the result back into self._rtc_info["temp_c"] so the
        telemetry scheduler always sends the latest displayed value.
        Returns float °C or None if the RTC is absent / unreadable.
        """
        if self._i2c is None:
            return None
        try:
            from src.drivers.ds3231 import DS3231
            rtc = DS3231(self._i2c, probe=False)
            tc = rtc.temperature()
            if tc is not None and self._rtc_info is not None:
                try:
                    self._rtc_info["temp_c"] = float(tc)
                except Exception:
                    pass
            return tc
        except Exception:
            return None

    # -------------------------------------------------
    # Drawing helpers
    # -------------------------------------------------

    def _draw_main_temp(self, temp_str, y):
        """Primary temperature centered horizontally in f_large + f_med unit."""
        f_l = self.oled.f_large
        f_m = self.oled.f_med
        if not temp_str:
            self.oled.draw_centered(f_l, "--.-", y)
            return

        w_num, h_large = self.oled._text_size(f_l, temp_str)
        w_c,   h_med   = self.oled._text_size(f_m, "C")
        deg_r = 2
        deg_w = deg_r * 2 + 1
        gap1  = 2
        gap2  = 2

        total_w = w_num + gap1 + deg_w + gap2 + w_c
        x = max(0, (self.oled.width - total_w) // 2)

        f_l.write(temp_str, x, y)
        x += w_num + gap1
        draw_degree(self.oled.oled, x, y + 6, r=deg_r, color=1)
        x += deg_w + gap2
        f_m.write("C", x, y + (h_large - h_med) // 2)

    def _draw_humidity(self, rh, y):
        """Bottom-left: RH 67%"""
        if rh is None:
            return
        try:
            rh_i = int(round(float(rh)))
            self.oled.f_med.write("RH {}%".format(rh_i), 2, y)
        except Exception:
            pass

    def _draw_rtc_temp(self, rtc_temp_c, y):
        """
        Bottom-right: [clock glyph] 33°C — integer, right-aligned.
        The clock glyph (9×9) is vertically centred within the f_med line height.
        """
        if rtc_temp_c is None:
            return
        try:
            t = int(round(float(rtc_temp_c)))
        except Exception:
            return

        f = self.oled.f_med
        val_str = "{}".format(t)
        w_val, h_val = self.oled._text_size(f, val_str)
        w_c,   _     = self.oled._text_size(f, "C")
        deg_r = 2
        deg_w = deg_r * 2 + 1
        gap   = 2

        # Total width: clock + gap + "33" + gap + "°" + gap + "C"
        total_w = CLOCK_W + gap + w_val + gap + deg_w + gap + w_c
        x = self.oled.width - total_w - 1
        if x < 0:
            x = 0

        # Clock glyph — vertically centred in the f_med line
        clock_y = y + max(0, (h_val - CLOCK_H) // 2)
        draw_clock(self.oled.oled, x, clock_y, color=1)
        x += CLOCK_W + gap

        # Temperature value
        f.write(val_str, x, y)
        x += w_val + gap

        # Degree circle
        draw_degree(self.oled.oled, x, y + 3, r=deg_r, color=1)
        x += deg_w + gap

        # Unit
        f.write("C", x, y)

    # -------------------------------------------------
    # Public API
    # -------------------------------------------------

    def show(self, reading=None, rtc_temp_c=None):
        """One-shot draw (non-blocking)."""
        self._draw_screen(reading, rtc_temp_c)

    def show_live(self, btn=None, air=None, get_reading=None, refresh_ms=None, tick_fn=None):
        """
        Live refresh screen.

        Supported calling styles:
          - show_live(btn=btn, air=air)
          - show_live(btn=btn, get_reading=callable)

        Exit: single click only.
        tick_fn: optional background callable (e.g. telemetry tick), called every 500ms.
        """
        if refresh_ms is None:
            refresh_ms = self.REFRESH_MS

        if get_reading is None:
            if air is not None:
                def get_reading():
                    try:
                        fn = getattr(air, "read_quick", None)
                        if callable(fn):
                            return fn(source="temp2")
                        return air.finish_sampling(log=False)
                    except Exception:
                        return None
            else:
                def get_reading():
                    return None

        # Fresh RTC temp on screen entry (not a stale boot value)
        rtc_temp_c = self._read_rtc_temp()

        next_refresh = 0
        reading = None
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

            if time.ticks_diff(now, next_refresh) >= 0:
                try:
                    reading = get_reading()
                except Exception:
                    reading = None

                # Refresh RTC temp every draw cycle
                try:
                    rtc_temp_c = self._read_rtc_temp()
                except Exception:
                    pass

                self._draw_screen(reading, rtc_temp_c)
                next_refresh = time.ticks_add(now, int(refresh_ms))

            action = None
            if btn is not None:
                try:
                    action = btn.poll_action()
                except Exception:
                    action = None

            if action == "single":
                return action

            time.sleep_ms(self.POLL_MS)

    # -------------------------------------------------
    # Core draw
    # -------------------------------------------------

    def _draw_screen(self, reading, rtc_temp_c=None):
        self.oled.oled.fill(0)

        f = self.oled.f_med
        _, h_med   = self.oled._text_size(f, "Ag")
        _, h_large = self.oled._text_size(self.oled.f_large, "8")

        # --- Connectivity icons (top-right) ---
        st = self._status
        _ch.draw(
            self.oled.oled,
            self.oled.width,
            gps_state=st.get("gps_on", GPS_NONE),
            api_connected=st.get("api_ok"),
            api_sending=bool(st.get("api_sending")),
            icon_y=1,
        )

        # --- Title top-left, no top margin ---
        f.write("Temperature 2", 0, 0)

        # --- Primary temp from SCD41 ---
        temp_c = None
        if reading:
            temp_c = getattr(reading, "scd41_temp_c", None)

        if temp_c is not None:
            temp_c = self._round_1dp(temp_c)
            self._cached_temp_c = temp_c
        else:
            temp_c = self._cached_temp_c  # hold last good value during SCD41 inter-sample gap

        temp_str = self._format_temp(temp_c)

        y_top        = h_med + 2
        y_bottom_row = self.oled.height - h_med - 1
        available    = y_bottom_row - y_top
        y_val        = y_top + max(0, (available - h_large) // 2)

        self._draw_main_temp(temp_str, y_val)

        # --- Bottom-left: SCD41 Humidity ---
        rh = None
        if reading:
            rh = getattr(reading, "scd41_humidity", None)

        if rh is not None:
            self._cached_rh = rh
        else:
            rh = self._cached_rh  # hold last good value
        self._draw_humidity(rh, y_bottom_row)

        # --- Bottom-right: clock glyph + RTC chip temperature ---
        self._draw_rtc_temp(rtc_temp_c, y_bottom_row)

        self.oled.oled.show()
