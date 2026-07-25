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

        self._load_config()

    # ----------------------------
    # Config
    # ----------------------------

    def _load_config(self):
        try:
            from config import load_config
            cfg = load_config() or {}
            self.enabled = bool(cfg.get("gps_enabled", False))
        except Exception:
            self.enabled = False

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

        # Short read each iteration (50 ms at most)
        self._consume_short(gps, max_ms=50)

        # Done once min_ms has elapsed
        if time.ticks_diff(now, self._check_deadline_ms) >= 0:
            self._check_active = False
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

        o.f_med.write(status_text[:18], 0, data_y)

        if self.enabled and not self._check_active:
            if self.last_fix and self.last_lat is not None and self.last_lon is not None:
                o.f_med.write("LAT:{:.4f}".format(self.last_lat), 0, data_y + line_h)
                o.f_med.write("LON:{:.4f}".format(self.last_lon), 0, data_y + line_h * 2)
            elif self._hw_present:
                sats = "--" if self.last_sats is None else str(int(self.last_sats))
                o.f_med.write("Sats: " + sats, 0, data_y + line_h)

        self.toggle.draw(fb, on=self.enabled)
        fb.show()

    # ----------------------------
    # Public entry
    # ----------------------------

    def show_live(self, gps, btn):
        """
        Single click : advance carousel.
        Double click : toggle GPS enabled and re-check if turning on.
        """
        btn.reset()
        self._load_config()
        self._clear_data()

        try:
            if gps:
                gps.enable()
        except Exception:
            pass
        gc.collect()

        # Always animate the GPS check on entry (at least 1 s)
        self._start_animated_check(min_ms=1000)
        self._draw(gps_flash=True)

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
                if not gps:
                    time.sleep_ms(25)
                    continue

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

                btn.reset()

            # Drive the animated check
            if self._check_active:
                done = self._tick_animated_check(gps)
                if done:
                    self._draw()   # settle on final icon state

            time.sleep_ms(25)
