# src/ui/screens/sleep.py  (MicroPython / Pico-safe)

import time
from src.ui.toggle import ToggleSwitch

try:
    from src.ui import connection_header as _ch
    from src.ui.connection_header import GPS_NONE
except Exception:
    _ch = None
    GPS_NONE = 0


class SleepScreen:
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
        self._toggle_on = False

        # Button LED — pulsed while sleeping
        self._led = None
        try:
            from machine import Pin
            from src.hal.board import btn_led_pin
            self._led = Pin(int(btn_led_pin()), Pin.OUT)
        except Exception:
            pass

    def _set_led(self, on):
        if self._led is None:
            return
        try:
            self._led.value(1 if on else 0)
        except Exception:
            pass

    def _draw(self, sleeping=False, waking=False):
        o = self.oled
        fb = o.oled
        fb.fill(0)

        # Connectivity icons: top-right (all off — sleep context)
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

        # Title: "Zz-Mode" in f_arvo20, left-aligned
        o.f_arvo20.write("Zz-Mode", 0, self._top_pad)

        # Body text below title
        try:
            _, title_h = o._text_size(o.f_arvo20, "Ag")
        except Exception:
            title_h = 17
        try:
            _, med_h = o._text_size(o.f_med, "Ag")
        except Exception:
            med_h = 11

        body_y = self._top_pad + title_h + 7   # extra space above body

        if sleeping:
            o.f_med.write("Good night!", 0, body_y)
        elif waking:
            o.f_med.write("Good morning!", 0, body_y)
        else:
            o.f_med.write("Dbl-click to sleep", 0, body_y)
            o.f_med.write("Sgl-click to wake", 0, body_y + med_h + 2)

        self.toggle.draw(fb, on=self._toggle_on)
        fb.show()

    def _do_sleep(self, btn, tick_fn):
        """Block until single click. OLED off. LED: 11 s off / 1 s on pulse."""
        self.oled.poweroff()

        # Start with LED off; first pulse fires after 11 s
        led_state = False
        self._set_led(False)
        next_led_toggle = time.ticks_add(time.ticks_ms(), 11000)

        tick_next = time.ticks_add(time.ticks_ms(), 500)

        try:
            while True:
                now = time.ticks_ms()

                # Asymmetric pulse: 11 s off → 1 s on → 11 s off → …
                if time.ticks_diff(now, next_led_toggle) >= 0:
                    led_state = not led_state
                    self._set_led(led_state)
                    next_led_toggle = time.ticks_add(now, 1000 if led_state else 11000)

                # Background telemetry tick every 500 ms
                if tick_fn is not None and time.ticks_diff(now, tick_next) >= 0:
                    try:
                        tick_fn()
                    except Exception:
                        pass
                    tick_next = time.ticks_add(now, 500)

                try:
                    action = btn.poll_action()
                except Exception:
                    action = None

                if action == "single":
                    break

                time.sleep_ms(25)
        finally:
            self._set_led(False)
            self.oled.poweron()

    def show_live(self, btn, tick_fn=None):
        try:
            btn.reset()
        except Exception:
            pass

        self._toggle_on = False
        self._draw()

        tick_next = time.ticks_add(time.ticks_ms(), 500)

        while True:
            now = time.ticks_ms()

            if tick_fn is not None and time.ticks_diff(now, tick_next) >= 0:
                try:
                    tick_fn()
                except Exception:
                    pass
                tick_next = time.ticks_add(now, 500)

            try:
                action = btn.poll_action()
            except Exception:
                action = None

            if action == "single":
                return "single"

            if action == "double":
                self._toggle_on = True
                self._set_led(True)                # LED on during goodnight message
                self._draw(sleeping=True)          # "Good night!" + toggle ON
                time.sleep_ms(3500)                # hold for 3.5 s
                self._set_led(False)
                self._do_sleep(btn, tick_fn)       # blocks until single click

                # Wake confirmation
                self._toggle_on = False
                self._draw(waking=True)            # "Good morning!"
                time.sleep_ms(2000)
                self._draw()                       # back to normal idle screen

            time.sleep_ms(25)
