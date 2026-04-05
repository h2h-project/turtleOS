# src/ui/screens/battery.py  (MicroPython / Pico-safe)

import time

try:
    from src.ui import connection_header as _ch
    from src.ui.connection_header import GPS_NONE
except Exception:
    _ch = None
    GPS_NONE = 0


class BatteryScreen:
    def __init__(self, oled, i2c=None):
        self.oled = oled
        self._i2c = i2c
        self._top_pad = 0

    def _sensor_present(self):
        if self._i2c is None:
            return False
        try:
            from src.drivers.ina219 import INA219
            return INA219.probe(self._i2c, addr=0x40)
        except Exception:
            return False

    def _usb_present(self):
        try:
            from src.hal.board import usb_power_present
            return usb_power_present()
        except Exception:
            return False

    def _draw(self):
        o = self.oled
        fb = o.oled
        fb.fill(0)

        # Connectivity icons: top-right
        if _ch:
            try:
                _ch.draw(
                    fb,
                    o.width,
                    gps_state=GPS_NONE,
                    wifi_ok=False,
                    api_connected=False,
                    api_sending=False,
                    icon_y=1,
                )
            except Exception:
                pass

        # Title: "Battery" in f_arvo20, left-aligned
        o.f_arvo20.write("Battery", 0, self._top_pad)

        # Measure layout
        try:
            _, title_h = o._text_size(o.f_arvo20, "Ag")
        except Exception:
            title_h = 17
        try:
            _, med_h = o._text_size(o.f_med, "Ag")
        except Exception:
            med_h = 11

        body_y = self._top_pad + title_h + 7

        sensor_on = self._sensor_present()
        usb_on = self._usb_present()

        o.f_med.write("Sensor: " + ("On" if sensor_on else "Off"), 0, body_y)
        o.f_med.write("USB power: " + ("On" if usb_on else "Off"), 0, body_y + med_h + 2)

        fb.show()

    def show_live(self, btn):
        try:
            btn.reset()
        except Exception:
            pass

        self._draw()

        while True:
            try:
                action = btn.poll_action()
            except Exception:
                action = None

            if action == "single":
                return "single"

            time.sleep_ms(25)
