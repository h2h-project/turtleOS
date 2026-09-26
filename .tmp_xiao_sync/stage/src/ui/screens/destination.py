# src/ui/screens/destination.py — Destination target display + capture (turtle mode)
#
# NORMAL view shows the active nav target with fallback:
#   name  = set_full_name  or mission_dest_full_name
#   coord = set_destination or mission_destination
#
# Nested click flow (single click on NORMAL still advances the carousel):
#   NORMAL  --double--> MENU
#   MENU    1x = target = here    -> stamp GPS into set_destination, then hand
#                                    off to the Journey screen ("Ready to go?")
#           2x = target = mission -> copy mission_destination into set_destination
#           3x = cancel           -> flash "Cancelled", NORMAL
#
# The action you repeat most (stamp) is one click; cancel is deliberately the
# multi-click so a slow double can't silently back out of the menu.
#
# A stamp writes config.json (authoritative for nav) and best-effort PATCHes
# the values to the server for the dashboard.
#
# set_departure / set_arrival used to be stamped here via two extra screens
# (DEPARTURE, ARRIVAL) after the destination stamp. That is now the Journey
# screen's job: departure is stamped when a journey opens, arrival when it
# ends (src/ui/screens/journey.py). Setting a destination therefore chains
# straight into the Journey screen so "pick a target, then go" is one flow.

import time

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


class DestinationScreen:
    def __init__(self, oled):
        self.oled = oled
        self._cfg = {}
        self._state = "normal"

    # ------------------------------------------------------------------ config

    def _load_config(self):
        try:
            from config import load_config
            self._cfg = load_config() or {}
        except Exception:
            if not isinstance(self._cfg, dict):
                self._cfg = {}

    def _save(self, updates):
        """Merge `updates` into config.json and refresh self._cfg."""
        try:
            from config import load_config, save_config
            c = load_config() or {}
            c.update(updates)
            save_config(c)
            self._cfg = c
        except Exception:
            # Keep the in-RAM copy consistent even if the write failed.
            try:
                self._cfg.update(updates)
            except Exception:
                pass
        self._push(updates)

    def _push(self, updates):
        """Best-effort mirror of the set_* fields to the server."""
        try:
            from src.net.device_client import patch_set_fields
            patch_set_fields(self._cfg, {
                k: v for k, v in updates.items()
                if k in ("set_destination", "set_departure", "set_arrival",
                         "set_waypoints", "set_short_name", "set_full_name")
            })
        except Exception:
            pass

    # ------------------------------------------------------------------ drawing

    def _wh(self):
        o = self.oled
        return int(getattr(o, "width", 128)), int(getattr(o, "height", 64))

    def _draw_normal(self):
        o = self.oled
        if o is None:
            return
        fb = o.oled
        fb.fill(0)
        w, h = self._wh()

        if _ch:
            try:
                _ch.draw(fb, w, gps_state=_ch.get_gps_state(), icon_y=1)
            except Exception:
                pass

        o.f_arvo20.write("Destination", 0, 0)

        c = self._cfg
        name = str(c.get("set_full_name") or c.get("mission_dest_full_name") or "")
        pair = _valid_pair(c.get("set_destination")) or _valid_pair(c.get("mission_destination"))

        y = 20
        if name:
            o.f_med.write(name[:18], 0, y)
            y += 12

        if pair is not None:
            o.f_small.write("LAT:{:.4f}".format(pair[0]), 0, y)
            y += 9
            o.f_small.write("LON:{:.4f}".format(pair[1]), 0, y)
        else:
            o.f_small.write("LAT: --", 0, y)
            y += 9
            o.f_small.write("LON: --", 0, y)

        o.f_small.write("+/- 5km accuracy", 0, h - 8)
        fb.show()

    def _draw_lines(self, title, lines):
        o = self.oled
        if o is None:
            return
        fb = o.oled
        fb.fill(0)
        o.f_arvo20.write(title, 0, 0)
        y = 26
        for ln in lines:
            o.f_small.write(ln, 0, y)
            y += 10
        fb.show()

    def _flash(self, text, ms, tick_fn=None):
        """Blocking centred message for `ms`. Nothing else runs during it."""
        o = self.oled
        if o is not None:
            fb = o.oled
            fb.fill(0)
            try:
                o.draw_centered(o.f_med, text, 26)
            except Exception:
                o.f_med.write(text, 0, 26)
            fb.show()
        _sleep = 0
        while _sleep < int(ms):
            time.sleep_ms(40)
            _sleep += 40
        if tick_fn is not None:
            try:
                tick_fn()
            except Exception:
                pass

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

    # ------------------------------------------------------------------ actions

    def _stamp_here(self, gps, key, label, tick_fn):
        """Stamp the current GPS position into config key `key`.
        Returns True on success, False when there is no fix."""
        loc = self._read_gps(gps)
        if loc is None:
            print("[DEST] {}: no GPS fix".format(key))
            self._flash("Sorry, no GPS!", 2000, tick_fn)
            return False
        updates = {key: [loc[0], loc[1]]}
        if key == "set_destination":
            updates["set_short_name"] = "SET"
            updates["set_full_name"] = "User Set"
        self._save(updates)
        print("[DEST] {} = {:.6f},{:.6f}".format(key, loc[0], loc[1]))
        self._flash(label, 1200, tick_fn)
        return True

    def _use_mission(self, tick_fn):
        try:
            from config import load_config
            c = load_config() or {}
        except Exception:
            c = self._cfg
        md = _valid_pair(c.get("mission_destination"))
        if md is None:
            self._flash("No mission dest!", 2000, tick_fn)
            return
        self._save({
            "set_destination": [md[0], md[1]],
            "set_short_name": str(c.get("mission_dest_short_name") or ""),
            "set_full_name": str(c.get("mission_dest_full_name") or ""),
        })
        self._flash("Destination Set!", 1200, tick_fn)

    def _launch_journey(self, btn, gps, tick_fn):
        """Hand off to the Journey screen right after a here-stamp so the
        operator can open the journey (which stamps set_departure) without
        hunting for the screen in the carousel."""
        try:
            from src.ui.screens.journey import JourneyScreen
            JourneyScreen(self.oled).show_live(btn, gps=gps, cfg=self._cfg,
                                               tick_fn=tick_fn)
        except Exception as e:
            print("[DEST] journey launch err:", repr(e))

    # ------------------------------------------------------------------ states

    _MENUS = {
        "menu": ("Set Target?", ("1x  target = here",
                                 "2x  target = mission",
                                 "3x  cancel")),
    }

    def _enter(self, state, btn):
        """Switch state, drop any clicks queued during a flash, redraw."""
        self._state = state
        try:
            btn.reset()
        except Exception:
            pass
        if state == "normal":
            self._draw_normal()
        else:
            title, lines = self._MENUS[state]
            self._draw_lines(title, lines)

    # ------------------------------------------------------------------ loop

    def show_live(self, btn, gps=None, cfg=None, tick_fn=None):
        try:
            btn.reset()
        except Exception:
            pass

        if isinstance(cfg, dict):
            self._cfg = cfg
        self._load_config()
        self._state = "normal"

        # NOTE: this screen used to shrink btn.click_window_ms to 350 ms for
        # "snappier" 2x/3x. That budget is the gap allowed *between* clicks,
        # and a deliberate human double-click often exceeds 350 ms release-to-
        # press — so the first click timed out as "single", which in NORMAL
        # state immediately advances the carousel. Result: double-click
        # appeared dead and you could never reach the menu. Left at the
        # tested 500 ms default that every other screen uses.

        self._draw_normal()
        while True:
            try:
                action = btn.poll_action()
            except Exception:
                action = None

            if self._state == "normal":
                if action == "single":
                    return "single"
                if action == "double":
                    self._enter("menu", btn)
                elif action == "quad":
                    return "quad"
                elif action == "sleep":
                    return "sleep"

            elif self._state == "menu":
                if action == "single":
                    ok = self._stamp_here(gps, "set_destination",
                                          "Destination Set!", tick_fn)
                    if ok:
                        self._launch_journey(btn, gps, tick_fn)
                    self._enter("normal", btn)
                elif action == "double":
                    self._use_mission(tick_fn)
                    self._enter("normal", btn)
                elif action == "triple":
                    self._flash("Cancelled", 900, tick_fn)
                    self._enter("normal", btn)
                elif action == "sleep":
                    return "sleep"

            if tick_fn is not None:
                try:
                    tick_fn()
                except Exception:
                    pass

            time.sleep_ms(25)
