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
# The side effects (fix, journey record, config, server mirror, nav state) live
# in src.app.actions.journey_start / journey_end, shared with the Bluetooth
# command handler; this screen only maps their result codes to a flash.
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


class JourneyScreen:
    def __init__(self, oled):
        self.oled = oled

        w = int(getattr(oled, "width", 128))
        h = int(getattr(oled, "height", 64))
        tx, ty, tw, th = 100, 16, 24, 40
        if tx + tw > w:
            tw = max(1, w - tx)
        if ty + th > h:
            th = max(1, h - ty)
        self.toggle = ToggleSwitch(x=tx, y=ty, w=tw, h=th) if ToggleSwitch else None

    # ------------------------------------------------------------------ journey

    def _is_active(self):
        try:
            from src.app.actions import journey_active
            return journey_active()
        except Exception:
            return False

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

    # Result code -> (flash text, ms). Anything unlisted is a generic failure.
    _START_MSGS = {
        0x00: ("Journey started!", 1200),   # OK
        0x20: ("Sorry, no GPS!", 2000),     # ERR_NO_GPS_FIX
        0x21: ("No clock yet!", 2000),      # ERR_RTC_NOT_SYNCED
        0x15: ("Already on a trip", 1600),  # ERR_WRONG_STATE
    }

    def _start_journey(self, gps, tick_fn):
        from src.app import actions
        code, _info = actions.journey_start(gps)
        text, ms = self._START_MSGS.get(code, ("Journey failed", 2000))
        self._flash(text, ms, tick_fn)

    def _end_journey(self, gps, tick_fn):
        from src.app import actions
        code, info = actions.journey_end(gps)
        if code == actions.OK:
            if info.get("arrival_stamped"):
                self._flash("Arrived!", 1200, tick_fn)
            else:
                self._flash("Arrived (no GPS)", 1600, tick_fn)
        elif code == actions.ERR_WRONG_STATE:
            self._flash("No trip open", 1600, tick_fn)
        else:
            self._flash("Journey failed", 2000, tick_fn)

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
