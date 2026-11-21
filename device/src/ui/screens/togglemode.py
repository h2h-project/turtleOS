# src/ui/screens/togglemode.py
#
# turtleOS <-> airOS switch, reached by five clicks on the waiting screen or a
# triple click on the Version screen. Review B7: the switch used to happen on
# the gesture alone, and quint fires on the fifth release with no window, so a
# bouncing or wet button could drop a turtle out of turtle mode (no
# NavController) mid-mission. It now asks first:
#
#     Switch to airOS?
#     Click to cancel.
#     Hold to confirm.
#
# Only a 3 s hold (the button's "sleep" gesture) switches. Any click, or
# CONFIRM_TIMEOUT_MS with no input, cancels.
import time

CONFIRM_TIMEOUT_MS = 20000
_POLL_MS = 5
_TICK_MS = 500


def _fit_font(oled, text, fonts):
    """First font in `fonts` whose rendering of `text` fits the screen width."""
    for f in fonts:
        try:
            w, _h = oled._text_size(f, text)
            if w <= oled.width:
                return f, w
        except Exception:
            pass
    f = fonts[-1]
    try:
        return f, oled._text_size(f, text)[0]
    except Exception:
        return f, 0


def _draw_lines(oled, lines):
    """lines: [(text, y, fonts)] — each centred, in the first font that fits."""
    if oled is None:
        return
    fb = oled.oled
    fb.fill(0)
    for text, y, fonts in lines:
        fonts = [f for f in fonts if f is not None]
        if not fonts:
            continue
        f, w = _fit_font(oled, text, fonts)
        try:
            f.write(text, max(0, (oled.width - w) // 2), y)
        except Exception:
            pass
    try:
        fb.show()
    except Exception:
        pass


def _confirm(oled, btn, target_name, tick_fn=None):
    """Show the confirmation prompt. True only on a hold."""
    big = [getattr(oled, "f_arvo16", None), getattr(oled, "f_med", None),
           getattr(oled, "f_small", None)]
    small = [getattr(oled, "f_med", None), getattr(oled, "f_small", None)]
    _draw_lines(oled, [
        ("Switch to {}?".format(target_name), 6, big),
        ("Click to cancel.", 32, small),
        ("Hold to confirm.", 48, small),
    ])

    try:
        btn.reset()
    except Exception:
        pass

    start = time.ticks_ms()
    last_tick = start
    while True:
        a = btn.poll_action()
        if a == "sleep":
            return True
        if a is not None:
            print("[TOGGLEMODE] cancelled ({})".format(a))
            return False
        now = time.ticks_ms()
        if time.ticks_diff(now, start) >= CONFIRM_TIMEOUT_MS:
            print("[TOGGLEMODE] cancelled (timeout)")
            return False
        if tick_fn is not None and time.ticks_diff(now, last_tick) >= _TICK_MS:
            last_tick = now
            try:
                tick_fn()
            except Exception:
                pass
        time.sleep_ms(_POLL_MS)


def show_toggle_mode(oled, current_turtle_mode, btn, tick_fn=None):
    """Ask to flip turtle_mode; on a hold, save it, show it for 2 s and reboot.

    Call with the CURRENT value of turtle_mode; the screen flips it. Returns
    False if cancelled (the caller carries on); never returns on a switch.
    """
    new_turtle_mode = not current_turtle_mode
    target_name = "turtleOS" if new_turtle_mode else "airOS"

    if btn is None or not _confirm(oled, btn, target_name, tick_fn=tick_fn):
        if btn is None:
            print("[TOGGLEMODE] no button to confirm with; not switching")
        _draw_lines(oled, [("Cancelled", 24, [getattr(oled, "f_arvo16", None),
                                              getattr(oled, "f_med", None)])])
        time.sleep_ms(800)
        try:
            if btn is not None:
                btn.reset()
        except Exception:
            pass
        return False

    try:
        from src.app import actions
        code, _info = actions.set_turtle_mode(new_turtle_mode)
    except Exception as e:
        print("[TOGGLEMODE] save failed:", repr(e))
        code, actions = None, None

    if actions is None or code != actions.OK:
        print("[TOGGLEMODE] save failed: code", code)
        _draw_lines(oled, [("Switch failed", 24, [getattr(oled, "f_arvo16", None),
                                                  getattr(oled, "f_med", None)])])
        time.sleep_ms(1500)
        try:
            btn.reset()
        except Exception:
            pass
        return False

    _draw_lines(oled, [
        (target_name, 14, [getattr(oled, "f_arvo16", None), getattr(oled, "f_med", None)]),
        ("activated", 36, [getattr(oled, "f_arvo16", None), getattr(oled, "f_med", None)]),
    ])
    time.sleep_ms(2000)
    import machine
    machine.reset()
