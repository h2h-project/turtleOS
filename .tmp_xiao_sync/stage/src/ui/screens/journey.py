# src/ui/screens/journey.py — start / end an operator journey (turtle mode)
#
# WHAT THIS SCREEN IS FOR
#   A "journey" marks a leg of travel the operator cares about — a pond test,
#   a field run, a release-and-track. While a journey is active, every
#   telemetry payload the scheduler builds is tagged with the journey's id
#   (flags.journey_id, added in telemetry_scheduler._build_full_payload). The
#   hopeturtles.org my-turtle map can then highlight exactly those GPS points.
#   The turtle keeps recording and sending telemetry as normal in the
#   background — the tag is the only thing this screen changes.
#
# STRUCTURE  (same skeleton as wifi.py: a right-hand ToggleSwitch, double-click
#   flips it; single-click advances the carousel)
#
#   No active journey:
#       title  "Journey"
#       body   "Ready to go?"
#       toggle OFF
#       2x click -> needs a GPS fix; stamps set_departure, opens the journey,
#                   toggle ON, flashes "Journey started!"
#
#   Active journey (this screen becomes the FIRST single-click screen, ahead of
#   Destination — see flows.sensor_carousel):
#       title  "Journey"
#       body   "Trip in progress" / "Double click to" / "arrive"
#       toggle ON
#       2x click -> stamps set_arrival (best-effort — a lost fix does not trap
#                   the operator in the journey), closes the journey, toggle
#                   OFF, flashes "Arrived!"
#
# The authoritative journey record lives in /journey_state.json via
# src.app.journey; set_departure / set_arrival are mirrored to config.json and
# best-effort PATCHed to the server for the dashboard, exactly like the
# Destination screen's set_* fields.
#
# OPTIONAL SERVER-SIDE STRAGGLER BACK-FILL (not implemented — noted here so it
#   can be picked up later if it proves necessary):
#   A telemetry reading built while the RTC was briefly unsynced is dropped
#   before it is ever sent, and a reading built in the gap between "journey
#   opened" and the journey id reaching the scheduler cache could in principle
#   go out untagged. If that ever shows up as missing points on a highlighted
#   route, the fix is a one-shot server UPDATE when the journey ends:
#       UPDATE telemetry_tb
#          SET raw_data = JSON_SET(raw_data, '$.flags.journey_id', ?)
#        WHERE turtle_id = ?
#          AND `timestamp` BETWEEN <started_at> AND <ended_at>
#          AND JSON_EXTRACT(raw_data, '$.flags.journey_id') IS NULL
#   keyed on the journey's started_at / ended_at unix seconds. Belt-and-braces
#   only; the in-payload tag is authoritative.

import time

try:
    from src.ui.toggle import ToggleSwitch
except Exception:
    ToggleSwitch = None

try:
    from src.ui import connection_header as _ch
except Exception:
    _ch = None


def _valid_pair(val):
    if isinstance(val, (list, tuple)) and len(val) == 2:
        try:
            la = float(val[0]); lo = float(val[1])
            if -90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0:
                return (la, lo)
        except Exception:
            pass
    return None


class JourneyScreen:
    def __init__(self, oled):
        self.oled = oled
        self._cfg = {}

        w = int(getattr(oled, "width", 128))
        h = int(getattr(oled, "height", 64))
        tx, ty, tw, th = 100, 16, 24, 40
        if tx + tw > w:
            tw = max(1, w - tx)
        if ty + th > h:
            th = max(1, h - ty)
        self.toggle = ToggleSwitch(x=tx, y=ty, w=tw, h=th) if ToggleSwitch else None

    # ------------------------------------------------------------------ config

    def _load_config(self, cfg=None):
        if isinstance(cfg, dict):
            self._cfg = cfg
            return
        try:
            from config import load_config
            self._cfg = load_config() or {}
        except Exception:
            if not isinstance(self._cfg, dict):
                self._cfg = {}

    def _save(self, updates):
        """Merge `updates` into config.json and best-effort mirror to server."""
        try:
            from config import load_config, save_config
            c = load_config() or {}
            c.update(updates)
            save_config(c)
            self._cfg = c
        except Exception:
            try:
                self._cfg.update(updates)
            except Exception:
                pass
        try:
            from src.net.device_client import patch_set_fields
            patch_set_fields(self._cfg, {
                k: v for k, v in updates.items()
                if k in ("set_departure", "set_arrival")
            })
        except Exception:
            pass

    # ------------------------------------------------------------------ journey

    def _journey(self):
        try:
            from src.app import journey
            return journey
        except Exception:
            return None

    def _is_active(self):
        j = self._journey()
        try:
            return bool(j and j.active())
        except Exception:
            return False

    # --------------------------------------------------------------- nav state

    def _enter_sail_nav(self):
        """Opening a journey puts the turtle into SAIL-NAV — from here on it is
        steering to the active target and the waiting-screen flèche fills in.
        No-op (and harmless) if the state machine refuses the transition, e.g.
        the turtle is in SAFE after a fault."""
        try:
            from src.nav import state_machine as sm
            if sm.get_state() == sm.BOOT:
                # Boot normally advances BOOT->ACQUIRE; cover the race where the
                # journey screen is reached first.
                sm.set_state(sm.ACQUIRE, "journey started (from boot)")
            sm.set_state(sm.SAIL_NAV, "journey started")
        except Exception as e:
            print("[JOURNEY] sail-nav enter failed:", repr(e))

    def _stand_down(self):
        """Ending a journey drops SAIL-NAV back to ACQUIRE. Left alone if the
        turtle has meanwhile gone to SAFE or already reached ARRIVAL."""
        try:
            from src.nav import state_machine as sm
            if sm.get_state() == sm.SAIL_NAV:
                sm.set_state(sm.ACQUIRE, "journey ended")
        except Exception as e:
            print("[JOURNEY] stand-down failed:", repr(e))

    # ------------------------------------------------------------------ GPS

    def _read_gps(self, gps):
        """Return (lat, lon) from a live/recent fix, or None."""
        try:
            from src.nav import gpsfix
        except Exception:
            gpsfix = None

        if gpsfix is not None:
            try:
                la, lo, age = gpsfix.get()
                if la is not None and age is not None and age < 5000:
                    return (la, lo)
            except Exception:
                pass

        if gpsfix is None or gps is None:
            return None

        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < 2200:
            try:
                line = gps.read_nmea(max_ms=40)
            except Exception:
                line = None
            if line and "RMC" in line:
                la, lo, cog = gpsfix.parse_rmc(line)
                if la is not None:
                    try:
                        gpsfix.update(la, lo, cog)
                    except Exception:
                        pass
                    return (la, lo)
            time.sleep_ms(10)
        return None

    # ------------------------------------------------------------------ drawing

    def _draw(self):
        o = self.oled
        if o is None:
            return
        fb = o.oled
        fb.fill(0)

        if _ch:
            try:
                _ch.draw(fb, o.width, gps_state=_ch.get_gps_state(), icon_y=1)
            except Exception:
                pass

        o.f_arvo20.write("Journey", 0, 0)

        active = self._is_active()
        y = 26
        if active:
            for ln in ("Trip in progress", "Double click to", "arrive"):
                o.f_med.write(ln, 0, y)
                y += 13
        else:
            o.f_med.write("Ready to go?", 0, y)

        if self.toggle:
            try:
                self.toggle.draw(fb, on=active)
            except Exception:
                pass
        fb.show()

    def _flash(self, text, ms, tick_fn=None):
        o = self.oled
        if o is not None:
            fb = o.oled
            fb.fill(0)
            try:
                o.draw_centered(o.f_med, text, 26)
            except Exception:
                o.f_med.write(text, 0, 26)
            fb.show()
        waited = 0
        while waited < int(ms):
            time.sleep_ms(40)
            waited += 40
        if tick_fn is not None:
            try:
                tick_fn()
            except Exception:
                pass

    # ------------------------------------------------------------------ actions

    def _start_journey(self, gps, tick_fn):
        loc = self._read_gps(gps)
        if loc is None:
            print("[JOURNEY] start: no GPS fix")
            self._flash("Sorry, no GPS!", 2000, tick_fn)
            return
        j = self._journey()
        rec = None
        try:
            rec = j.start(loc[0], loc[1]) if j else None
        except Exception as e:
            print("[JOURNEY] start err:", repr(e))
        if rec is None:
            # No synced RTC → no usable id. Nothing to tag against.
            self._flash("No clock yet!", 2000, tick_fn)
            return
        self._save({"set_departure": [loc[0], loc[1]]})
        self._enter_sail_nav()
        print("[JOURNEY] started id={} dep={:.6f},{:.6f}".format(
            rec.get("id"), loc[0], loc[1]))
        self._flash("Journey started!", 1200, tick_fn)

    def _end_journey(self, gps, tick_fn):
        loc = self._read_gps(gps)
        j = self._journey()
        jid = None
        try:
            jid = j.active_id() if j else None
        except Exception:
            pass
        if loc is not None:
            self._save({"set_arrival": [loc[0], loc[1]]})
        try:
            if j:
                j.end()
        except Exception as e:
            print("[JOURNEY] end err:", repr(e))
        self._stand_down()
        if loc is not None:
            print("[JOURNEY] arrived id={} arr={:.6f},{:.6f}".format(
                jid, loc[0], loc[1]))
            self._flash("Arrived!", 1200, tick_fn)
        else:
            print("[JOURNEY] arrived id={} (no GPS fix — set_arrival unchanged)".format(jid))
            self._flash("Arrived (no GPS)", 1600, tick_fn)

    def _toggle(self, gps, tick_fn):
        if self._is_active():
            self._end_journey(gps, tick_fn)
        else:
            self._start_journey(gps, tick_fn)

    # ------------------------------------------------------------------ loop

    def show_live(self, btn, gps=None, cfg=None, tick_fn=None):
        """single -> advance carousel ("single"); double -> start/end journey."""
        try:
            btn.reset()
        except Exception:
            pass

        self._load_config(cfg)
        self._draw()

        _tick_next = time.ticks_ms()
        while True:
            now = time.ticks_ms()
            if tick_fn is not None and time.ticks_diff(now, _tick_next) >= 0:
                try:
                    tick_fn()
                except Exception:
                    pass
                _tick_next = time.ticks_add(now, 500)

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
                self._toggle(gps, tick_fn)
                try:
                    btn.reset()
                except Exception:
                    pass
                self._draw()

            time.sleep_ms(25)
