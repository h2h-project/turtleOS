# src/ui/screens/bluetooth.py — Turtle Wrangler Bluetooth on/off (turtle mode)
#
# Connectivity carousel: Online -> Logging -> WiFi -> Bluetooth -> Device.
# Same skeleton as wifi.py / journey.py: a right-hand ToggleSwitch,
# double-click flips it, single-click advances.
#
#   Toggle OFF  "Off"           ble_enabled = false: radio never activated.
#                               This is the mission lockdown — only this
#                               screen turns it back on.
#   Toggle ON   "Open 9:42"     advertising; countdown of the wrangle window
#               "Open"          ble_window_min = 0 (always advertising)
#               "Connected"     a phone is connected (header shows the rune)
#               "Hidden"        window closed, no phone (rare here: opening
#                               this screen reopens the window)
#
# Opening the screen reopens the wrangle window — someone standing at the hull
# looking at Bluetooth is the intent. The on/off itself is
# src.app.actions.ble_set_enabled, shared with the BLE_SET_ENABLED command.
#
# PAIRING (Phase 4): the pairing gate is open only while this screen shows.
# When a phone starts pairing, the 6-digit passkey replaces the view, large,
# to be typed into the phone. Anywhere else the turtle refuses to pair.
# Triple-click -> "Forget phones?" (double-click confirms) clears every bond.

import time

try:
    from src.ui.toggle import ToggleSwitch
except Exception:
    ToggleSwitch = None

try:
    from src.ui import connection_header as _ch
except Exception:
    _ch = None


class BluetoothScreen:
    REDRAW_MS = 350

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

    @staticmethod
    def _svc():
        try:
            from src.net import ble_service
            return ble_service.instance()
        except Exception:
            return None

    def _lines(self, svc):
        """(big line, small line, toggle_on)."""
        if svc is None:
            return "Unavailable", "No Bluetooth here", False
        st = svc.state()
        name = svc.name[:22]
        if st == "off":
            return "Off", "Double-click to turn on", False
        if st == "connected":
            if svc.link_bonded():
                return "Connected", "Paired - " + name[:14], True
            return "Connected", name, True
        if st == "advertising":
            rem = svc.window_remaining_s()
            if rem is None:
                return "Open", name, True
            return "Open {}:{:02d}".format(rem // 60, rem % 60), name, True
        return "Hidden", name, True

    def _draw_passkey(self, pk):
        o = self.oled
        fb = o.oled
        fb.fill(0)
        o.f_arvo20.write("Pair code", 0, 0)
        code = "%06d" % pk
        try:
            o.draw_centered(o.f_large, code, 24)
        except Exception:
            o.f_med.write(code, 0, 26)
        try:
            o.draw_centered(o.f_small, "Type this on the phone", 54)
        except Exception:
            pass
        fb.show()

    def _draw(self):
        o = self.oled
        if o is None:
            return
        svc = self._svc()
        pk = svc.passkey() if svc is not None else None
        if pk is not None:
            self._draw_passkey(pk)
            return
        fb = o.oled
        fb.fill(0)
        if _ch:
            try:
                _ch.draw(fb, o.width, gps_state=_ch.get_gps_state(), icon_y=1)
            except Exception:
                pass
        # Same title font/position as Online and WiFi (78 px wide in Arvo 20 —
        # clear of the header icons, which start at x=89).
        o.f_arvo20.write("Bluetooth", 0, 0)

        big, small, on = self._lines(self._svc())
        o.f_med.write(big, 0, 26)
        o.f_small.write(small, 0, 44)

        if self.toggle:
            try:
                self.toggle.draw(fb, on=on)
            except Exception:
                pass
        fb.show()

    def _flash(self, text, ms):
        o = self.oled
        if o is None:
            return
        fb = o.oled
        fb.fill(0)
        try:
            o.draw_centered(o.f_med, text, 26)
        except Exception:
            o.f_med.write(text, 0, 26)
        fb.show()
        time.sleep_ms(int(ms))

    def _toggle_ble(self):
        from src.app import actions
        svc = self._svc()
        want = not (svc is not None and svc.active())
        code, _info = actions.ble_set_enabled(want)
        if code == actions.ERR_NO_HARDWARE:
            self._flash("No Bluetooth", 1200)
        elif code != actions.OK:
            self._flash("Not saved!", 1500)

    def _confirm_forget(self, btn, tick_fn):
        """Triple-click: ask before clearing every stored bond."""
        svc = self._svc()
        if svc is None:
            return
        n = svc.bond_count()
        o = self.oled
        fb = o.oled
        fb.fill(0)
        o.f_arvo20.write("Forget phones?", 0, 0)
        o.f_small.write("%d phone%s paired" % (n, "" if n == 1 else "s"), 0, 26)
        o.f_small.write("2x click: forget all", 0, 40)
        o.f_small.write("1x click: keep", 0, 52)
        fb.show()
        try:
            btn.reset()
        except Exception:
            pass
        deadline = time.ticks_add(time.ticks_ms(), 10_000)
        _tick_next = time.ticks_ms()
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
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
            if action == "double":
                svc.forget_bonds()
                self._flash("Phones forgotten", 1500)
                return
            if action is not None:
                break
            time.sleep_ms(25)
        self._flash("Kept", 800)

    def show_live(self, btn, tick_fn=None):
        """single -> advance; double -> on/off; triple -> forget phones."""
        try:
            btn.reset()
        except Exception:
            pass

        from src.app import actions
        actions.ble_open_window()
        svc = self._svc()
        if svc is not None:
            svc.set_pairing_allowed(True)
        try:
            return self._loop(btn, tick_fn)
        finally:
            if svc is not None:
                svc.set_pairing_allowed(False)

    def _loop(self, btn, tick_fn):
        self._draw()

        _tick_next = time.ticks_ms()
        _draw_next = time.ticks_add(time.ticks_ms(), self.REDRAW_MS)
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
            if action in ("double", "triple"):
                if action == "double":
                    self._toggle_ble()
                else:
                    self._confirm_forget(btn, tick_fn)
                try:
                    btn.reset()
                except Exception:
                    pass
                self._draw()
                _draw_next = time.ticks_add(time.ticks_ms(), self.REDRAW_MS)

            # Countdown, connect/disconnect changes and the header "+" blink
            # (1 s phase while advertising) — 350 ms keeps the blink even.
            if time.ticks_diff(time.ticks_ms(), _draw_next) >= 0:
                self._draw()
                _draw_next = time.ticks_add(time.ticks_ms(), self.REDRAW_MS)

            time.sleep_ms(25)
