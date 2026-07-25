# src/app/boot_guard.py
#
# Two triggers halt the boot pipeline and activate debug mode:
#   1. A file named "debug_mode" exists on flash.
#   2. The button is held for HOLD_MS at power-on.
#
# In debug mode the OLED shows "De-Bug Mode / Click to reboot".
# Boot stops; a polling loop waits:
#   - Button click  → machine.reset() (normal boot)
#   - Ctrl-C        → loop exits → caller raises SystemExit → REPL live

import time

_DEBUG_FILE = "debug_mode"
_HOLD_MS    = 2000
_POLL_MS    = 25
_BTN_GPIO   = 4      # fallback if HAL unavailable


def _btn_gpio():
    try:
        from src.hal.board import btn_pin
        return btn_pin()
    except Exception:
        return _BTN_GPIO


def _show(oled, line1, line2=None):
    if oled is None:
        return
    try:
        fb = oled.oled
        fb.fill(0)
        f1 = getattr(oled, "f_arvo20", None) or getattr(oled, "f_large", None)
        f2 = getattr(oled, "f_med",    None) or getattr(oled, "f_small", None)
        if f1:
            oled.draw_centered(f1, line1, 15)
        if f2 and line2:
            oled.draw_centered(f2, line2, 38)
        fb.show()
    except Exception:
        pass


def _stable_release(btn, stable_ms=150):
    """Block until btn has been continuously HIGH for stable_ms (debounced release)."""
    while True:
        while btn.value() == 0:
            time.sleep_ms(_POLL_MS)
        # Confirm it stays high for the full stable window
        ok = True
        deadline = time.ticks_add(time.ticks_ms(), stable_ms)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            time.sleep_ms(_POLL_MS)
            if btn.value() == 0:
                ok = False
                break
        if ok:
            return


def _debug_loop(btn):
    """
    Show the debug screen and wait.
    Click → machine.reset()
    Ctrl-C → returns so caller can raise SystemExit (REPL becomes live).
    """
    _stable_release(btn)

    try:
        while True:
            if btn.value() == 0:          # press detected
                time.sleep_ms(50)         # debounce
                if btn.value() == 0:      # still pressed
                    while btn.value() == 0:
                        time.sleep_ms(_POLL_MS)
                    from machine import reset
                    reset()
            time.sleep_ms(_POLL_MS)
    except KeyboardInterrupt:
        raise                             # propagate — KeyboardInterrupt is BaseException,
                                          # not caught by main.py's except Exception → REPL


def check(oled=None):
    """
    Returns True if debug mode was triggered.
    Caller should raise SystemExit to hand the REPL to the user.
    """
    gpio = _btn_gpio()
    try:
        from machine import Pin
        btn = Pin(gpio, Pin.IN, Pin.PULL_UP)
    except Exception:
        btn = None

    # --- trigger 1: debug_mode file on flash ---
    try:
        import os as _os
        _os.stat(_DEBUG_FILE)
        print("[BOOT] debug_mode file — debug mode, click to reboot")
        _show(oled, "De-Bug Mode", "Click to reboot")
        if btn is not None:
            _debug_loop(btn)
        return True
    except OSError:
        pass

    # --- trigger 2: button held at power-on ---
    if btn is None or btn.value() != 0:
        return False   # button not pressed — fast path, no delay

    _show(oled, "Hold...", "Release to cancel")
    print("[BOOT] Button held — waiting {}ms...".format(_HOLD_MS))

    start = time.ticks_ms()
    held = True
    while time.ticks_diff(time.ticks_ms(), start) < _HOLD_MS:
        if btn.value() != 0:
            held = False
            break
        time.sleep_ms(_POLL_MS)

    if not held:
        return False

    print("[BOOT] Debug mode — click to reboot, Ctrl-C for REPL")
    _show(oled, "De-Bug Mode", "Click to reboot")
    _debug_loop(btn)
    return True
