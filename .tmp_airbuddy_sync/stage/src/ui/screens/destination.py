# src/ui/screens/destination.py — Destination target display (turtle mode only)

import time

try:
    from src.ui import connection_header as _ch
except Exception:
    _ch = None


class DestinationScreen:
    def __init__(self, oled):
        self.oled = oled
        self._cfg = {}

    def _load_config(self):
        try:
            from config import load_config
            self._cfg = load_config() or {}
        except Exception:
            pass

    def _draw(self):
        o = self.oled
        if o is None:
            return
        fb = o.oled
        fb.fill(0)

        w = int(getattr(o, "width", 128))
        h = int(getattr(o, "height", 64))

        if _ch:
            try:
                _ch.draw(fb, w, gps_state=_ch.get_gps_state(), icon_y=1)
            except Exception:
                pass

        # Title
        o.f_arvo20.write("Destination", 0, 0)

        # Config values
        name = str(self._cfg.get("dest_name", "") or "")
        coord = self._cfg.get("dest_coord", [None, None])
        try:
            lat = float(coord[0]) if coord[0] is not None else None
            lon = float(coord[1]) if coord[1] is not None else None
        except Exception:
            lat = None
            lon = None

        y = 20

        if name:
            o.f_med.write(name[:18], 0, y)
            y += 12

        if lat is not None:
            o.f_small.write("LAT:{:.4f}".format(lat), 0, y)
        else:
            o.f_small.write("LAT: --", 0, y)
        y += 9

        if lon is not None:
            o.f_small.write("LON:{:.4f}".format(lon), 0, y)
        else:
            o.f_small.write("LON: --", 0, y)

        # Accuracy note — bottom of screen
        o.f_small.write("+/- 5km accuracy", 0, h - 8)

        fb.show()

    def show_live(self, btn, tick_fn=None):
        try:
            btn.reset()
        except Exception:
            pass

        self._load_config()
        self._draw()

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

            if tick_fn is not None:
                try:
                    tick_fn()
                except Exception:
                    pass

            time.sleep_ms(25)
