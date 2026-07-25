# src/ui/screens/battery.py  (MicroPython / Pico-safe)

import time

try:
    from src.ui import connection_header as _ch
    from src.ui.connection_header import GPS_NONE
except Exception:
    _ch = None
    GPS_NONE = 0


def _fmt_v(v):
    if v is None:
        return "---"
    return "{:.2f}V".format(float(v))


def _fmt_ma(v):
    if v is None:
        return "---"
    return "{:.0f}mA".format(float(v))


def _fmt_mw(v):
    if v is None:
        return "---"
    return "{:.0f}mW".format(float(v))


class BatteryScreen:
    def __init__(self, oled, i2c=None, ina=None):
        self.oled = oled
        self._i2c = i2c
        self._ina = ina          # pre-shared instance; lazy init skipped if set
        self._refresh_ms = 3000

    # ----------------------------------------------------------
    # INA219 access
    # ----------------------------------------------------------
    def _get_ina(self):
        if self._ina is not None:
            return self._ina
        if self._i2c is None:
            return None
        try:
            from src.drivers.ina219 import INA219
            ina = INA219(self._i2c, auto_init=True)
            if ina.is_present:
                self._ina = ina
        except Exception:
            pass
        return self._ina

    def _read(self):
        ina = self._get_ina()
        if ina is None or not ina.is_present:
            return {"present": False, "bus_v": None, "current_ma": None, "power_mw": None}
        try:
            return {
                "present": True,
                "bus_v":      ina.bus_voltage_v(),
                "current_ma": ina.current_ma(),
                "power_mw":   ina.power_mw(),
            }
        except Exception:
            return {"present": False, "bus_v": None, "current_ma": None, "power_mw": None}

    # ----------------------------------------------------------
    # Render
    # ----------------------------------------------------------
    def _draw(self, data):
        from src.ui.glyphs import draw_battery_v, battery_status, _BATTERY_LEVEL_BANDS

        o  = self.oled
        fb = o.oled
        fb.fill(0)

        # Connection header (top-right)
        if _ch:
            try:
                # Assume all previous values
                _ch.draw(fb, o.width, gps_state=_ch.get_gps_state(), icon_y=1)
            except Exception:
                pass

        # Title
        o.f_arvo20.write("Battery", 0, 0)

        try:
            _, title_h = o._text_size(o.f_arvo20, "Ag")
        except Exception:
            title_h = 17
        try:
            _, med_h = o._text_size(o.f_med, "Ag")
        except Exception:
            med_h = 11

        body_y  = title_h + 4
        present = data.get("present", False)
        bus_v   = data.get("bus_v")

        # ---- Left: three f_med lines ----
        line_gap = med_h + 3
        if present:
            o.f_med.write(_fmt_v(bus_v),                   0, body_y)
            o.f_med.write(_fmt_ma(data.get("current_ma")), 0, body_y + line_gap)
            o.f_med.write(_fmt_mw(data.get("power_mw")),   0, body_y + 2 * line_gap)
        else:
            o.f_med.write("No INA219", 0, body_y)

        # ---- Right: vertical battery glyph ----
        batt_w      = 22
        batt_nub_h  = 3
        # Fill from body_y to near the screen bottom (3 px margin)
        batt_body_h = max(20, 64 - body_y - batt_nub_h - 3)
        batt_x      = o.width - batt_w - 2   # 2 px right margin
        batt_y      = body_y

        status = battery_status(bus_v) if present and bus_v is not None else "critical"
        bands  = _BATTERY_LEVEL_BANDS.get(status, 0)

        draw_battery_v(
            fb, batt_x, batt_y,
            bands_filled=bands,
            total_bands=5,
            w=batt_w,
            h=batt_body_h,
            nub_w=8,
            nub_h=batt_nub_h,
        )

        fb.show()

    # ----------------------------------------------------------
    # Live loop
    # ----------------------------------------------------------
    def show_live(self, btn):
        try:
            btn.reset()
        except Exception:
            pass

        data = self._read()
        self._draw(data)

        _next = time.ticks_add(time.ticks_ms(), self._refresh_ms)

        while True:
            try:
                action = btn.poll_action()
            except Exception:
                action = None

            if action == "single":
                return "single"

            now = time.ticks_ms()
            if time.ticks_diff(now, _next) >= 0:
                data  = self._read()
                self._draw(data)
                _next = time.ticks_add(now, self._refresh_ms)

            time.sleep_ms(25)
