# src/ui/screens/time.py

import time
import json
import gc
import machine


try:
    import urequests
except Exception:
    urequests = None

try:
    from src.ui import connection_header as _ch
    from src.ui.connection_header import GPS_NONE
except Exception:
    _ch = None
    GPS_NONE = 0

try:
    from src.ui.glyphs import draw_clock, CLOCK_W, CLOCK_H
except Exception:
    draw_clock = None
    CLOCK_W = 9
    CLOCK_H = 9

CONFIG_FILE = "config.json"

MONTHS = [
    "January","February","March","April","May","June",
    "July","August","September","October","November","December"
]


class TimeScreen:
    def __init__(self, oled, cfg, wifi_manager=None, rtc_info=None, ds3231=None, status=None):
        self.oled = oled
        self.cfg = cfg

        # Optional helpers
        self.wifi = wifi_manager
        self.rtc_info = rtc_info if isinstance(rtc_info, dict) else {}
        self.ds3231 = ds3231
        self.status = status if isinstance(status, dict) else {}

        # API refresh happens once per screen open
        self._tz_checked = False

    # -------------------------------------------------
    # RTC / tuples
    # -------------------------------------------------
    def _get_utc_tuple(self):
        # System RTC is expected to be UTC
        return time.localtime()

    def _get_user_time_tuple(self):
        offset_min = self.cfg.get("timezone_offset_min", None)
        if offset_min is None:
            return None

        try:
            offset_min = int(offset_min)
        except Exception:
            return None

        utc_ts = time.time()
        local_ts = utc_ts + (offset_min * 60)
        return time.localtime(local_ts)

    def _sync_rtc_from_server_ts(self, ts_ms):
        """
        Set system RTC to UTC using server epoch milliseconds.
        Safe no-op on any error.
        """
        try:
            epoch_s = int(ts_ms) // 1000
            # MicroPython epoch is 2000-01-01; subtract Unix→MP offset before gmtime.
            # Without this, time.gmtime interprets a Unix timestamp ~30 years in the future.
            mp_s = epoch_s - 946684800
            y, mo, d, hh, mm, ss, wday, _ = time.gmtime(mp_s)
            machine.RTC().datetime((y, mo, d, wday, hh, mm, ss, 0))
        except Exception:
            pass

    # -------------------------------------------------
    # API refresh (once) + offline-safe gating
    # -------------------------------------------------
    def _refresh_from_api_once(self):
        if self._tz_checked:
            return
        self._tz_checked = True

        if not urequests:
            return

        if not self.cfg.get("wifi_enabled", False):
            return

        # Require actual WiFi connection (prevents blocking offline)
        if self.wifi:
            try:
                if not self.wifi.is_connected():
                    return
            except Exception:
                return

        api_base = self.cfg.get("api_base")
        device_id = self.cfg.get("device_id")
        device_key = self.cfg.get("device_key")

        if not (api_base and device_id and device_key):
            return

        url = api_base.rstrip("/") + "/v1/device?compact=1"
        headers = {
            "X-Device-Id": str(device_id),
            "X-Device-Key": str(device_key),
        }

        resp = None
        try:
            gc.collect()
            resp = urequests.get(url, headers=headers, timeout=4)

            if resp.status_code != 200:
                return

            data = resp.json()
            if not isinstance(data, dict) or not data.get("ok"):
                return

            # timezone offset update (cache in config.json)
            tz_off = data.get("timezone_offset_min", None)
            if tz_off is None:
                tz_off = data.get("tz_offset_min", None)

            if tz_off is not None:
                try:
                    tz_off = int(tz_off)

                    old = self.cfg.get("timezone_offset_min", None)
                    try:
                        old = int(old) if old is not None else None
                    except Exception:
                        old = None

                    if old != tz_off:
                        self.cfg["timezone_offset_min"] = tz_off
                        with open(CONFIG_FILE, "w") as f:
                            json.dump(self.cfg, f)
                except Exception:
                    pass

            # RTC sync from server epoch
            ts_ms = data.get("ts", None)
            if ts_ms is not None:
                self._sync_rtc_from_server_ts(ts_ms)

        except Exception:
            pass
        finally:
            try:
                if resp:
                    resp.close()
            except Exception:
                pass
            gc.collect()

    # -------------------------------------------------
    # Formatting helpers
    # -------------------------------------------------
    def _ordinal(self, n):
        if 10 <= n % 100 <= 20:
            suf = "th"
        else:
            suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return str(n) + suf

    def _fmt_date_long(self, t):
        day = self._ordinal(int(t[2]))
        month = MONTHS[int(t[1]) - 1]
        year = int(t[0])
        return "{} {}, {}".format(month, day, year)

    def _fmt_time_blink(self, t):
        # clean 1Hz blink: colon visible on even seconds
        s = int(t[5])
        colon = ":" if (s % 2 == 0) else " "
        return "{:02d}{}{:02d}".format(int(t[3]), colon, int(t[4]))

    def _fmt_tz_offset(self):
        """Returns UTC offset string: "+7", "-5:30", or "UTC"."""
        offset_min = self.cfg.get("timezone_offset_min", None)
        if offset_min is None:
            return "UTC"
        try:
            offset_min = int(offset_min)
        except Exception:
            return "UTC"
        if offset_min == 0:
            return "UTC"
        h = offset_min // 60
        m = abs(offset_min % 60)
        if m == 0:
            return "{:+d}".format(h)
        return "{:+d}:{:02d}".format(h, m)

    def _fmt_date_short(self, t):
        """Returns compact date string e.g. '8 Mar 2026'."""
        _MONTHS = ["Jan","Feb","Mar","Apr","May","Jun",
                   "Jul","Aug","Sep","Oct","Nov","Dec"]
        d = int(t[2])
        m = _MONTHS[int(t[1]) - 1]
        y = int(t[0])
        return "{} {} {}".format(d, m, y)

    # -------------------------------------------------
    # Top-left UTC
    # -------------------------------------------------
    def _draw_top_left_utc(self, y=1):
        utc = self._get_utc_tuple()
        utc_str = "{:02d}:{:02d} UTC".format(int(utc[3]), int(utc[4]))
        self.oled.f_med.write(utc_str, 2, y)

    # -------------------------------------------------
    # Render
    # -------------------------------------------------
    def _render(self):
        fb = self.oled.oled
        ow = self.oled.width
        oh = self.oled.height

        fb.fill(0)

        # --- Top-right: connection icons ---
        if _ch:
            try:
                _ch.draw(
                    fb, ow,
                    gps_state=self.status.get("gps_on", GPS_NONE),
                    wifi_ok=bool(self.status.get("wifi_ok", False)),
                    api_connected=bool(self.status.get("api_ok", False)),
                    api_sending=bool(self.status.get("api_sending", False)),
                )
            except Exception:
                pass

        # --- Measure fonts ---
        _, h_med   = self.oled._text_size(self.oled.f_med,   "Ag")
        _, h_small = self.oled._text_size(self.oled.f_small, "Ag")
        _, h_large = self.oled._text_size(self.oled.f_large, "8")

        # --- Top-left: UTC time ---
        self._draw_top_left_utc(y=1)

        # --- Bottom band geometry (clock glyph is 9px tall) ---
        _band_h = max(CLOCK_H, h_med)
        y_band  = oh - _band_h - 1          # top of bottom band
        y_bot   = y_band + (_band_h - h_med) // 2   # vertically center text in band

        # --- Main time: horizontally centered ---
        user_t = self._get_user_time_tuple()

        if user_t is None:
            # No timezone configured — fall back to UTC (all chars valid for f_large)
            _display_t = self._get_utc_tuple()
            time_str = self._fmt_time_blink(_display_t)
            colon_str = "{:02d}:{:02d}".format(int(_display_t[3]), int(_display_t[4]))
        else:
            _display_t = user_t
            time_str = self._fmt_time_blink(user_t)
            # Use the colon version as reference so x never shifts during blink
            colon_str = "{:02d}:{:02d}".format(int(user_t[3]), int(user_t[4]))
        w_ref = self.oled._text_size(self.oled.f_large, colon_str)[0]

        x_time = max(0, (ow - w_ref) // 2)

        # Vertically center between UTC row and bottom band
        y_top  = h_med + 4
        y_time = y_top + max(0, (y_band - y_top - h_large) // 2)

        try:
            self.oled.f_large.write(time_str, x_time, y_time)
        except Exception:
            self.oled.draw_centered(self.oled.f_large, time_str, y_time)

        # --- Bottom-left: clock glyph + TZ offset ---
        clock_x = 2
        clock_y = y_band   # align top of clock with top of band
        if draw_clock:
            try:
                draw_clock(fb, clock_x, clock_y, color=1)
            except Exception:
                pass

        tz_str = self._fmt_tz_offset()
        self.oled.f_med.write(tz_str, clock_x + CLOCK_W + 3, y_bot)

        # --- Bottom-right: compact date, right-aligned ---
        _date_t = user_t if user_t is not None else self._get_utc_tuple()
        if _date_t is not None:
            date_str = self._fmt_date_short(_date_t)
            try:
                w_date, _ = self.oled._text_size(self.oled.f_med, date_str)
                x_date = max(0, ow - w_date - 2)
            except Exception:
                x_date = 0
            self.oled.f_med.write(date_str, x_date, y_bot)

        fb.show()

    # -------------------------------------------------
    # Public
    # -------------------------------------------------
    def show_live(self, btn=None, max_seconds=8, tick_fn=None):
        # Try API once on entry; safe no-op offline
        self._refresh_from_api_once()

        start_ms = time.ticks_ms()

        # Entry settle: drain any tail click so user can exit immediately
        if btn:
            try:
                t0 = time.ticks_ms()
                while time.ticks_diff(time.ticks_ms(), t0) < 120:
                    try:
                        btn.poll_action()
                    except Exception:
                        pass
                    time.sleep_ms(15)
            except Exception:
                pass

        # Redraw throttling: update when the second changes (or at least every 350ms)
        last_sec = None
        last_draw_ms = 0

        # Poll fast so short taps register
        poll_ms = 25
        min_redraw_ms = 350
        _tick_next = time.ticks_ms()
        _tick_every = 500

        while True:
            now_ms = time.ticks_ms()

            if tick_fn is not None and time.ticks_diff(now_ms, _tick_next) >= 0:
                try:
                    tick_fn()
                except Exception:
                    pass
                _tick_next = time.ticks_add(now_ms, _tick_every)

            # --- Fast button polling ---
            if btn:
                try:
                    action = btn.poll_action()
                except Exception:
                    action = None
                if action:
                    return action

            # --- Decide whether to redraw ---
            try:
                utc = time.localtime()
                sec = int(utc[5])
            except Exception:
                sec = None

            redraw = False
            if sec is not None and sec != last_sec:
                redraw = True
                last_sec = sec
            elif time.ticks_diff(now_ms, last_draw_ms) >= min_redraw_ms:
                redraw = True

            if redraw:
                try:
                    self._render()
                except Exception as e:
                    print("[TIME] render error:", repr(e))
                last_draw_ms = now_ms

            # --- Exit timer ---
            if max_seconds:
                if time.ticks_diff(now_ms, start_ms) >= int(max_seconds) * 1000:
                    return None

            time.sleep_ms(poll_ms)