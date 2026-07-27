# src/ui/screens/gps.py  (MicroPython / Pico-safe)

import time
import gc

from src.ui.toggle import ToggleSwitch

try:
    from src.ui import connection_header as _ch
    from src.ui.connection_header import GPS_NONE, GPS_INIT, GPS_FIXED
except Exception:
    _ch = None
    GPS_NONE = 0
    GPS_INIT = 1
    GPS_FIXED = 2


class GPSScreen:
    def __init__(self, oled):
        self.oled = oled

        self._top_pad = 0

        w = int(getattr(oled, "width", 128))
        h = int(getattr(oled, "height", 64))

        tx = 100
        ty = 16 + self._top_pad
        tw = 24
        th = 40

        if tx + tw > w:
            tw = max(1, w - tx)
        if ty + th > h:
            th = max(1, h - ty)

        self.toggle = ToggleSwitch(x=tx, y=ty, w=tw, h=th)

        self.enabled = False
        self.last_fix = False
        self.last_lat = None
        self.last_lon = None
        self.last_sats = None

        # Hardware-present flag: True once any NMEA sentence arrives
        self._hw_present = False

        # Animated-check state
        self._check_active = False
        self._check_deadline_ms = 0
        self._next_gps_flash_ms = 0
        self._gps_flash_on = True

        # Text animation
        self._dot_phase = 0
        self._next_text_ms = 0
        self._status = ""

        # Manual telemetry registry state (only used when telemetry_mode=="manual")
        self._tel_mode = "auto"
        # None | "sending" | "ok" | "fail" | "stamped" | "nostamp"
        self._send_state = None
        self._send_result_until_ms = 0

        self._load_config()

    # ----------------------------
    # Config
    # ----------------------------

    def _load_config(self, cfg=None):
        try:
            if cfg is None:
                from config import load_config
                cfg = load_config() or {}
            self.enabled = bool(cfg.get("gps_enabled", False))
            mode = str(cfg.get("telemetry_mode", "auto") or "auto").strip().lower()
            self._tel_mode = mode if mode in ("off", "auto", "manual") else "auto"
        except Exception:
            self.enabled = False
            self._tel_mode = "auto"

    def _save_config(self):
        try:
            from config import load_config, save_config
            cfg = load_config() or {}
            cfg["gps_enabled"] = self.enabled
            save_config(cfg)
        except Exception:
            pass

    # ----------------------------
    # NMEA parsing helpers
    # ----------------------------

    def _nmea_degmin_to_deg(self, s, hemi):
        try:
            if not s or not hemi:
                return None
            dot = s.find(".")
            if dot < 0:
                return None
            deg_len = 2 if hemi in ("N", "S") else 3
            deg = int(s[:deg_len])
            minutes = float(s[deg_len:])
            val = deg + (minutes / 60.0)
            if hemi in ("S", "W"):
                val = -val
            return val
        except Exception:
            return None

    def _parse_rmc(self, line):
        try:
            p = line.split(",")
            if len(p) < 7:
                return
            self._hw_present = True
            self.last_fix = (p[2] == "A")
            if p[3] and p[4] and p[5] and p[6]:
                lat = self._nmea_degmin_to_deg(p[3], p[4])
                lon = self._nmea_degmin_to_deg(p[5], p[6])
                if lat is not None and lon is not None:
                    self.last_lat = lat
                    self.last_lon = lon
        except Exception:
            pass

    def _parse_gga(self, line):
        try:
            p = line.split(",")
            if len(p) < 8:
                return
            self._hw_present = True
            if p[6] and p[6] != "0":
                self.last_fix = True
            if p[7]:
                try:
                    self.last_sats = int(p[7])
                except Exception:
                    pass
            if p[2] and p[3] and p[4] and p[5]:
                lat = self._nmea_degmin_to_deg(p[2], p[3])
                lon = self._nmea_degmin_to_deg(p[4], p[5])
                if lat is not None and lon is not None:
                    self.last_lat = lat
                    self.last_lon = lon
        except Exception:
            pass

    def _clear_data(self):
        self.last_fix = False
        self.last_lat = None
        self.last_lon = None
        self.last_sats = None
        self._hw_present = False

    def _consume_short(self, gps, max_ms=60):
        """
        Short non-blocking NMEA read. Called repeatedly from the check loop
        so the display can update between reads.
        """
        if not gps:
            return
        try:
            t = time.ticks_ms()
            while time.ticks_diff(time.ticks_ms(), t) < int(max_ms):
                line = gps.read_nmea(max_ms=30)
                if not line:
                    return
                if "RMC" in line:
                    self._parse_rmc(line)
                elif "GGA" in line:
                    self._parse_gga(line)
        except Exception:
            pass

    # ----------------------------
    # Animated check
    # ----------------------------

    def _start_animated_check(self, min_ms=1000):
        """Begin the animated GPS check cycle."""
        now = time.ticks_ms()
        self._check_active = True
        self._check_deadline_ms = time.ticks_add(now, int(min_ms))
        self._next_gps_flash_ms = now   # first flash fires immediately
        self._gps_flash_on = True
        self._dot_phase = 0
        self._next_text_ms = now
        self._status = "Checking GPS"

    def _tick_animated_check(self, gps):
        """
        Drive the animated GPS check: short reads + icon flash every 300 ms.
        Call every main-loop iteration while _check_active is True.
        Returns True when the check phase is complete.
        """
        if not self._check_active:
            return True

        now = time.ticks_ms()
        redraw = False

        # Flash GPS icon every 300 ms
        if time.ticks_diff(now, self._next_gps_flash_ms) >= 0:
            self._next_gps_flash_ms = time.ticks_add(now, 300)
            self._gps_flash_on = not self._gps_flash_on
            redraw = True

        # Text dots every 400 ms
        if time.ticks_diff(now, self._next_text_ms) >= 0:
            self._next_text_ms = time.ticks_add(now, 400)
            self._dot_phase = (self._dot_phase + 1) % 4
            self._status = "Checking GPS" + "." * self._dot_phase
            redraw = True

        if redraw:
            self._draw(gps_flash=self._gps_flash_on)

        # Short read each iteration (20 ms at most) — kept brief so the button
        # is still sampled roughly every 30 ms during the check phase.
        self._consume_short(gps, max_ms=20)

        # Done once min_ms has elapsed
        if time.ticks_diff(now, self._check_deadline_ms) >= 0:
            self._check_active = False
            return True

        return False

    # ----------------------------
    # Manual registry line
    # ----------------------------

    def _pick_fitting(self, candidates):
        """
        Return the first candidate that fits left of the toggle in f_small.
        Avoids mid-word truncation by falling back to shorter wordings.
        """
        o = self.oled
        max_w = self.toggle.x - 4
        f = getattr(o, "f_small", None)
        if f is None:
            return candidates[-1]
        for text in candidates:
            try:
                tw, _ = o._text_size(f, text)
            except Exception:
                tw = len(text) * 5
            if tw <= max_w:
                return text
        return candidates[-1]

    def _registry_text(self):
        st = self._send_state
        if st == "sending":
            return self._pick_fitting(
                ("Stamping & Sending...", "Stamping&Sending...", "Sending...")
            )
        if st == "ok":
            return "Sent"
        if st == "fail":
            return "Send failed"
        if st == "stamped":
            # Handed to the background sender; outcome unknown from here.
            return "Stamped"
        if st == "nostamp":
            return self._pick_fitting(("Not stamped - no clock", "Not stamped"))
        return self._pick_fitting(("Manual Registry", "Manual Reg."))

    def _expire_send_result(self):
        """Clear a transient send result once its hold time is up."""
        if self._send_state in (None, "sending"):
            return False
        if time.ticks_diff(time.ticks_ms(), self._send_result_until_ms) >= 0:
            self._send_state = None
            return True
        return False

    # ----------------------------
    # Drawing
    # ----------------------------

    def _draw(self, gps_flash=None):
        """
        gps_flash : when not None, overrides GPS icon state for animation.
                    True  → GPS_INIT (half-full = "checking")
                    False → GPS_NONE (hollow  = "off / waiting")
        """
        o = self.oled
        fb = o.oled
        fb.fill(0)

        if _ch:
            try:
                # Determine GPS icon state
                if gps_flash is not None:
                    # Animation override: alternate INIT ↔ NONE
                    gps_state = GPS_INIT if gps_flash else GPS_NONE
                elif self._check_active:
                    gps_state = GPS_INIT
                elif self.last_fix:
                    gps_state = GPS_FIXED
                elif self._hw_present:
                    gps_state = GPS_INIT   # hardware found but no fix yet
                else:
                    gps_state = GPS_NONE

                # WiFi: live probe; API: cache — no overrides needed here
                _ch.draw(
                    fb,
                    o.width,
                    gps_state=gps_state,
                    icon_y=1,
                )
            except Exception:
                pass

        # Title
        title_y = self._top_pad
        o.f_arvo20.write("GPS", 0, title_y)

        try:
            _, title_h = o._text_size(o.f_arvo20, "Ag")
        except Exception:
            title_h = 20

        data_y = int(title_y + title_h + 4)
        line_h = 13

        # Status line
        if self._check_active:
            status_text = self._status
        elif not self.enabled:
            status_text = "GPS off"
        elif self.last_fix:
            status_text = "Fix acquired"
        elif self._hw_present:
            status_text = "No fix"
        else:
            status_text = "No GPS found"

        # Manual telemetry mode borrows the third data row for the registry
        # line, so the fix/lat/lon rows shift up one slot. There is no room for
        # a fourth row: data_y=22 with line_h=13 already ends at y=60 on a 64 px
        # panel. "Fix acquired" is dropped in manual mode — the header GPS icon
        # already conveys fix state, and lat/lon presence confirms it.
        manual = (self._tel_mode == "manual") and not self._check_active

        have_fix = (
            self.enabled
            and not self._check_active
            and self.last_fix
            and self.last_lat is not None
            and self.last_lon is not None
        )

        row = 0
        if manual and have_fix:
            o.f_med.write("LAT:{:.4f}".format(self.last_lat), 0, data_y)
            o.f_med.write("LON:{:.4f}".format(self.last_lon), 0, data_y + line_h)
            row = 2
        else:
            o.f_med.write(status_text[:18], 0, data_y)
            row = 1
            if self.enabled and not self._check_active:
                if have_fix:
                    o.f_med.write("LAT:{:.4f}".format(self.last_lat), 0, data_y + line_h)
                    o.f_med.write("LON:{:.4f}".format(self.last_lon), 0, data_y + line_h * 2)
                    row = 3
                elif self._hw_present:
                    sats = "--" if self.last_sats is None else str(int(self.last_sats))
                    o.f_med.write("Sats: " + sats, 0, data_y + line_h)
                    row = 2

        if manual and row <= 2:
            o.f_small.write(
                self._registry_text(),
                0,
                data_y + line_h * 2,
            )

        self.toggle.draw(fb, on=self.enabled)
        fb.show()

    # ----------------------------
    # Public entry
    # ----------------------------

    def _toggle_gps(self, gps, btn):
        """Flip gps_enabled, persist, and restart the check if turning on."""
        if not gps:
            return

        self.enabled = not self.enabled
        self._save_config()
        self._clear_data()

        if self.enabled:
            try:
                gps.enable()
            except Exception:
                pass
            self._start_animated_check(min_ms=1000)
            self._draw(gps_flash=True)
        else:
            try:
                gps.disable()
            except Exception:
                pass
            self._check_active = False
            self._draw()

        try:
            btn.reset()
        except Exception:
            pass

    def _manual_send(self, btn, cfg, telemetry):
        """
        Stamp and send one telemetry packet on demand.

        A TelemetryBackgroundProcess is normally attached, in which case tick()
        hands the payload to a background thread and returns None — so the
        outcome is read from api_state rather than a return value, and a timeout
        reports "Stamped" (the packet is committed to flash either way).
        """
        if telemetry is None:
            self._send_state = "fail"
            self._send_result_until_ms = time.ticks_add(time.ticks_ms(), 1500)
            self._draw()
            return

        self._send_state = "sending"
        self._draw()

        before_ms = None
        try:
            before_ms = telemetry.api_state.get("last_ms")
        except Exception:
            pass

        armed = False
        try:
            armed = bool(telemetry.send_manual())
        except Exception as _e:
            print("[GPS] manual send arm failed:", repr(_e))

        if armed:
            try:
                telemetry.tick(cfg)
            except Exception as _e:
                print("[GPS] manual send tick failed:", repr(_e))

        result = "fail" if not armed else "stamped"

        # No payload was built (RTC not yet synced, sampling in flight, or no
        # values) — nothing was recorded, so do not claim it was.
        if armed:
            try:
                if telemetry.manual_pending():
                    telemetry.clear_manual()
                    self._send_state = "nostamp"
                    self._send_result_until_ms = time.ticks_add(time.ticks_ms(), 1500)
                    self._draw()
                    try:
                        btn.reset()
                    except Exception:
                        pass
                    return
            except Exception:
                pass

        if armed:
            # Poll for an outcome, staying responsive to the button.
            deadline = time.ticks_add(time.ticks_ms(), 4000)
            while time.ticks_diff(time.ticks_ms(), deadline) < 0:
                try:
                    btn.poll_action()
                except Exception:
                    pass
                try:
                    st = telemetry.api_state
                    if st.get("last_ms") != before_ms and st.get("ok") is not None:
                        result = "ok" if st.get("ok") else "fail"
                        break
                except Exception:
                    pass
                try:
                    telemetry.tick(cfg)
                except Exception:
                    pass
                time.sleep_ms(100)

        self._send_state = result
        self._send_result_until_ms = time.ticks_add(time.ticks_ms(), 1500)
        self._draw()

        try:
            btn.reset()
        except Exception:
            pass

    def show_live(self, gps, btn, cfg=None, telemetry=None):
        """
        Single click : advance carousel.

        In manual telemetry mode:
          Double click : stamp and send one telemetry packet.
          Triple click : toggle GPS enabled.
        In auto/off mode:
          Double click : toggle GPS enabled (unchanged legacy behaviour).
        """
        btn.reset()
        self._load_config(cfg)
        self._clear_data()
        self._send_state = None

        try:
            if gps:
                gps.enable()
        except Exception:
            pass
        gc.collect()

        # This screen listens more sensitively than the rest of the UI: a
        # shorter click window makes double/triple stamping feel immediate.
        # Restored in the finally block on every exit path.
        _saved_window_ms = getattr(btn, "click_window_ms", None)
        try:
            if _saved_window_ms is not None:
                btn.click_window_ms = 350
        except Exception:
            _saved_window_ms = None

        try:
            # Always animate the GPS check on entry (at least 1 s)
            self._start_animated_check(min_ms=1000)
            self._draw(gps_flash=True)

            manual = (self._tel_mode == "manual")

            while True:
                try:
                    action = btn.poll_action()
                except Exception:
                    action = None

                if action == "single":
                    return "single"

                if action == "quad":
                    return "quad"

                if action == "sleep":
                    return "sleep"

                if action == "double":
                    if manual:
                        self._manual_send(btn, cfg, telemetry)
                    else:
                        self._toggle_gps(gps, btn)

                elif action == "triple" and manual:
                    self._toggle_gps(gps, btn)

                # Drive the animated check
                if self._check_active:
                    done = self._tick_animated_check(gps)
                    if done:
                        self._draw()   # settle on final icon state
                elif self._expire_send_result():
                    self._draw()

                time.sleep_ms(10)
        finally:
            if _saved_window_ms is not None:
                try:
                    btn.click_window_ms = _saved_window_ms
                except Exception:
                    pass
