# src/ui/grace.py — shared pre-check click window for connectivity screens
#
# Online/WiFi/Telemetry screens all draw last-known status immediately on
# entry, then would normally kick off a live check (HTTP POST, WiFi connect
# attempt) that can block button polling for several seconds. This gives the
# user a window to single-click straight past the screen before that check
# ever starts.

import time


def await_grace_window(btn, tick_fn=None, ms=2000, tick_every=500):
    """
    Poll the button for up to `ms` without starting a live check.

    Returns the action string ("single", "double", "quad", "sleep", ...) as
    soon as the button reports one, or None once the window elapses with no
    action — the caller should then proceed to the live check.
    """
    start = time.ticks_ms()
    tick_next = time.ticks_add(start, tick_every)

    while time.ticks_diff(time.ticks_ms(), start) < ms:
        now = time.ticks_ms()

        if tick_fn is not None and time.ticks_diff(now, tick_next) >= 0:
            try:
                tick_fn()
            except Exception:
                pass
            tick_next = time.ticks_add(now, tick_every)

        try:
            action = btn.poll_action()
        except Exception:
            action = None

        if action:
            return action

        time.sleep_ms(25)

    return None
